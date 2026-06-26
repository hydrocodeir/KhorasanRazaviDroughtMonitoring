"""
SDAT Python module.

Python conversion of the MATLAB Standardized Drought Analysis Toolbox (SDAT)
core logic for both vector and matrix workflows.
"""

from .core import (
    sdat_from_vector,
    sdat_from_vector_reference,
    sdat_from_matrix,
    compute_empirical_probability,
    normal_inverse_cdf,
)
from .io_utils import load_precip_txt, load_prec_mat
from .plotting import (
    plot_vector_index,
    plot_matrix_timestep,
    load_drought_colormap_from_mat,
)

__all__ = [
    "sdat_from_vector",
    "sdat_from_vector_reference",
    "sdat_from_matrix",
    "compute_empirical_probability",
    "normal_inverse_cdf",
    "load_precip_txt",
    "load_prec_mat",
    "plot_vector_index",
    "plot_matrix_timestep",
    "load_drought_colormap_from_mat",
]
