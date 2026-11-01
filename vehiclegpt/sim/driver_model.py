"""Stochastic driver: picks target speeds for city/highway and follows them with style-dependent acceleration."""
import numpy as np


class DriverModel:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.speed = 0.0
        self.target = 0.0
        self.hold = 0.0

    def step(self, dt, road, style="normal", derate=False, stopping=False):
        rng = self.rng
        if self.hold <= 0:
            if road == "city":
                self.target = float(rng.choice([0, 25, 30, 40, 50, 60]))
                self.hold = float(rng.integers(10, 60))
            else:
                self.target = float(rng.uniform(85, 125)) + (10 if style == "aggressive" else 0)
                self.hold = float(rng.integers(60, 240))
        self.hold -= dt
        target = 0.0 if stopping else (min(self.target, 60.0) if derate else self.target)
        acc = (1.5 if road == "city" else 1.0) * (1.8 if style == "aggressive" else 1.0) * 3.6  # km/h per s
        dv = float(np.clip(target - self.speed, -2 * acc * dt, acc * dt)) + rng.normal(0, 0.3 * dt ** 0.5)
        self.speed = max(0.0, self.speed + dv)
        return self.speed

    def hard_brake(self, dt):
        self.speed = max(0.0, self.speed - 18 * dt)
        return self.speed
