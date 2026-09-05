"""Trainer for the micro neural network.

Plain NumPy implementation (forward + manual backward + Adam). Training the
full model takes a few seconds on CPU and produces two small ``.npz`` files::

    python -m converter.brain.train             # train both heads
    python -m converter.brain.train --report    # also print a held-out report

The training set is synthesized deterministically (see :mod:`.dataset`), so the
weights are reproducible from a fixed seed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np

from . import dataset, labels
from .runtime import model_dir

HIDDEN1 = 256
HIDDEN2 = 128


def _gelu(x: np.ndarray) -> np.ndarray:
    return 0.5 * x * (1.0 + np.tanh(0.7978845608028654 * (x + 0.044715 * x**3)))


def _gelu_grad(x: np.ndarray) -> np.ndarray:
    c = 0.7978845608028654
    inner = c * (x + 0.044715 * x**3)
    t = np.tanh(inner)
    return 0.5 * (1.0 + t) + 0.5 * x * (1.0 - t**2) * c * (1.0 + 3 * 0.044715 * x**2)


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(axis=-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


class Adam:
    def __init__(self, params: dict[str, np.ndarray], lr: float = 2e-3) -> None:
        self.lr = lr
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}
        self.t = 0

    def step(self, params: dict[str, np.ndarray], grads: dict[str, np.ndarray]) -> None:
        self.t += 1
        b1, b2, eps = 0.9, 0.999, 1e-8
        for key, grad in grads.items():
            self.m[key] = b1 * self.m[key] + (1 - b1) * grad
            self.v[key] = b2 * self.v[key] + (1 - b2) * grad**2
            mhat = self.m[key] / (1 - b1**self.t)
            vhat = self.v[key] / (1 - b2**self.t)
            params[key] -= self.lr * mhat / (np.sqrt(vhat) + eps)


def init_params(in_dim: int, head_space: dict[str, tuple[str, ...]], seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)

    def he(shape: tuple[int, int]) -> np.ndarray:
        return (rng.standard_normal(shape) * np.sqrt(2.0 / shape[0])).astype(np.float32)

    params: dict[str, np.ndarray] = {
        "w1": he((in_dim, HIDDEN1)),
        "b1": np.zeros(HIDDEN1, dtype=np.float32),
        "w2": he((HIDDEN1, HIDDEN2)),
        "b2": np.zeros(HIDDEN2, dtype=np.float32),
    }
    for head, space in head_space.items():
        params[f"head_{head}_w"] = he((HIDDEN2, len(space)))
        params[f"head_{head}_b"] = np.zeros(len(space), dtype=np.float32)
    return params


def forward(params: dict[str, np.ndarray], x: np.ndarray, head_space: dict[str, tuple[str, ...]]):
    z1 = x @ params["w1"] + params["b1"]
    a1 = _gelu(z1)
    z2 = a1 @ params["w2"] + params["b2"]
    a2 = _gelu(z2)
    logits = {h: a2 @ params[f"head_{h}_w"] + params[f"head_{h}_b"] for h in head_space}
    return {"z1": z1, "a1": a1, "z2": z2, "a2": a2, "logits": logits}


def train_head_set(
    x: np.ndarray,
    ys: dict[str, np.ndarray],
    head_space: dict[str, tuple[str, ...]],
    epochs: int = 60,
    batch: int = 256,
    lr: float = 2e-3,
    seed: int = 7,
    weight_decay: float = 1e-5,
    verbose: bool = True,
) -> dict[str, np.ndarray]:
    n, in_dim = x.shape
    params = init_params(in_dim, head_space, seed)
    opt = Adam(params, lr)
    rng = np.random.default_rng(seed)

    for epoch in range(epochs):
        perm = rng.permutation(n)
        total = 0.0
        for start in range(0, n, batch):
            idx = perm[start : start + batch]
            xb = x[idx]
            cache = forward(params, xb, head_space)
            m = len(idx)

            grads = {k: np.zeros_like(v) for k, v in params.items()}
            da2 = np.zeros_like(cache["a2"])
            for head, space in head_space.items():
                probs = _softmax(cache["logits"][head])
                target = ys[head][idx]
                onehot = np.zeros_like(probs)
                onehot[np.arange(m), target] = 1.0
                total += float(-np.log(np.clip(probs[np.arange(m), target], 1e-9, None)).mean())
                dlogits = (probs - onehot) / m
                grads[f"head_{head}_w"] = cache["a2"].T @ dlogits
                grads[f"head_{head}_b"] = dlogits.sum(axis=0)
                da2 += dlogits @ params[f"head_{head}_w"].T

            dz2 = da2 * _gelu_grad(cache["z2"])
            grads["w2"] = cache["a1"].T @ dz2
            grads["b2"] = dz2.sum(axis=0)
            da1 = dz2 @ params["w2"].T
            dz1 = da1 * _gelu_grad(cache["z1"])
            grads["w1"] = xb.T @ dz1
            grads["b1"] = dz1.sum(axis=0)

            for key in ("w1", "w2"):
                grads[key] += weight_decay * params[key]
            opt.step(params, grads)

        if verbose and (epoch + 1) % 10 == 0:
            print(f"  epoch {epoch + 1:3d}/{epochs}  loss={total / max(1, n // batch):.4f}")
    return params


def evaluate(params: dict[str, np.ndarray], x: np.ndarray, ys: dict[str, np.ndarray], head_space) -> dict[str, float]:
    cache = forward(params, x, head_space)
    return {
        head: float((np.argmax(cache["logits"][head], axis=1) == ys[head]).mean())
        for head in head_space
    }


def train_all(out_dir: Path | None = None, samples: int = 12000, epochs: int = 60, report: bool = False) -> dict[str, Any]:
    out_dir = out_dir or model_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}

    for kind, gen, head_space, filename in (
        ("item", dataset.generate_item_samples, labels.ITEM_HEADS, "item_head.npz"),
        ("block", dataset.generate_block_samples, labels.BLOCK_HEADS, "block_head.npz"),
    ):
        print(f"[{kind}] building dataset ({samples} samples)…")
        x, ys = dataset.build_matrices(list(gen(samples)), kind)
        # Shuffle before splitting: augmentation emits variants of the same
        # sample consecutively, so a positional split would leak.
        order = np.random.default_rng(99).permutation(len(x))
        x = x[order]
        ys = {h: v[order] for h, v in ys.items()}
        split = int(len(x) * 0.9)
        xtr, xte = x[:split], x[split:]
        ytr = {h: v[:split] for h, v in ys.items()}
        yte = {h: v[split:] for h, v in ys.items()}

        print(f"[{kind}] training… dim={x.shape[1]}")
        params = train_head_set(xtr, ytr, head_space, epochs=epochs)
        acc = evaluate(params, xte, yte, head_space)
        results[kind] = acc
        np.savez_compressed(out_dir / filename, **params)
        print(f"[{kind}] saved {out_dir / filename}")
        for head, value in acc.items():
            print(f"    {head:<16} holdout accuracy {value * 100:.1f}%")

    if report:
        (out_dir / "training_report.json").write_text(
            __import__("json").dumps(results, indent=2), encoding="utf-8"
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the CraftEngine converter micro-brain")
    parser.add_argument("--out", type=Path, default=None, help="output directory for .npz weights")
    parser.add_argument("--samples", type=int, default=12000)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    train_all(args.out, args.samples, args.epochs, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
