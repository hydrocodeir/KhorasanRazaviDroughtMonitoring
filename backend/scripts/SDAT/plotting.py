"""
Plot helpers mirroring MATLAB demo plots.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def plot_vector_index(
    si: np.ndarray,
    ylabel: str = "Standardized Precipitation Index",
    xlabel: str = "time step",
) -> None:
    """
    MATLAB-like line plot for vector SDAT output.
    """
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise ImportError(
            "Plotting requires matplotlib. Install with: pip install matplotlib"
        ) from exc

    si = np.asarray(si, dtype=float).reshape(-1)
    plt.figure(figsize=(10, 4))
    plt.plot(si, linewidth=1.5)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.show()


def load_drought_colormap_from_mat(mat_path: str | Path) -> np.ndarray:
    """
    Load `mycmap` from MATLAB DroughtColormap.mat.
    """
    try:
        from scipy.io import loadmat  # type: ignore
    except Exception as exc:
        raise ImportError(
            "Loading MATLAB colormap requires SciPy. Install with: pip install scipy"
        ) from exc

    data = loadmat(mat_path)
    if "mycmap" not in data:
        raise KeyError(f"'mycmap' not found in {mat_path}.")
    return np.asarray(data["mycmap"], dtype=float)


def plot_matrix_timestep(
    si: np.ndarray,
    timestep: int = 2,
    title: str = "Standardized XXXX Index",
    colormap: np.ndarray | None = None,
    caxis: tuple[float, float] = (-3.0, 3.0),
) -> None:
    """
    MATLAB-like imagesc for one time step.

    `timestep` is 0-based in Python. MATLAB sample uses `SI(:,:,3)`, which is
    `timestep=2` here.
    """
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise ImportError(
            "Plotting requires matplotlib. Install with: pip install matplotlib"
        ) from exc

    si = np.asarray(si, dtype=float)
    if si.ndim != 3:
        raise ValueError("si must be 3D with shape (n, m, p0).")

    img = si[:, :, timestep]
    plt.figure(figsize=(6, 5))

    if colormap is not None:
        from matplotlib.colors import ListedColormap

        cmap = ListedColormap(colormap)
    else:
        cmap = "viridis"

    plt.imshow(img, cmap=cmap, vmin=caxis[0], vmax=caxis[1], aspect="auto")
    plt.title(title)
    plt.colorbar()
    plt.tight_layout()
    plt.show()
