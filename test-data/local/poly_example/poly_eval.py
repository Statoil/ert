#!/usr/bin/env python

import json
import time


def _load_coeffs(filename):
    with open(filename) as f:
        return json.load(f)


def _evaluate(coeffs, x):
    return coeffs["a"] * x ** 2 + coeffs["b"] * x + coeffs["c"]


if __name__ == "__main__":
    time.sleep(15)
    coeffs = _load_coeffs("coeffs.json")
    output = [_evaluate(coeffs, x) for x in range(10)]
    with open("poly_0.out", "w") as f:
        f.write("\n".join(map(str, output)))
