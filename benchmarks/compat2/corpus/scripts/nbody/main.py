import math


def energy(x: list[float], y: list[float], z: list[float], vx: list[float], vy: list[float], vz: list[float], m: list[float]) -> float:
    e = 0.0
    n = len(m)
    for i in range(n):
        e += 0.5 * m[i] * (vx[i] * vx[i] + vy[i] * vy[i] + vz[i] * vz[i])
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            dz = z[i] - z[j]
            e -= m[i] * m[j] / math.sqrt(dx * dx + dy * dy + dz * dz)
    return e


def advance(x: list[float], y: list[float], z: list[float], vx: list[float], vy: list[float], vz: list[float], m: list[float], dt: float) -> None:
    n = len(m)
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            dz = z[i] - z[j]
            d2 = dx * dx + dy * dy + dz * dz
            mag = dt / (d2 * math.sqrt(d2))
            vx[i] -= dx * m[j] * mag
            vy[i] -= dy * m[j] * mag
            vz[i] -= dz * m[j] * mag
            vx[j] += dx * m[i] * mag
            vy[j] += dy * m[i] * mag
            vz[j] += dz * m[i] * mag
    for i in range(n):
        x[i] += dt * vx[i]
        y[i] += dt * vy[i]
        z[i] += dt * vz[i]


def main() -> None:
    x = [0.0, 4.84, 8.34, 12.89, 15.37]
    y = [0.0, -1.16, 4.12, -15.11, -25.9]
    z = [0.0, -0.10, -0.4, -0.22, 0.17]
    vx = [0.0, 0.606, -1.01, 1.08, 0.979]
    vy = [0.0, 2.81, 1.82, 0.868, 0.594]
    vz = [0.0, -0.02, 0.008, -0.01, -0.034]
    m = [39.47, 0.037, 0.011, 0.0017, 0.002]
    print(f"{energy(x, y, z, vx, vy, vz, m):.9f}")
    for _ in range(2000):
        advance(x, y, z, vx, vy, vz, m, 0.01)
    print(f"{energy(x, y, z, vx, vy, vz, m):.9f}")


main()
