"""
hildaplus — read, aggregate, model and draw the HILDA+ land use record.

    sources    dataset constants, class table, regions, cache paths
    remote     pull one year out of PANGAEA's 4.5 GB ZIP by HTTP range
    raster     GeoTIFF reads: whole, windowed, or reduced to a coarse grid
    cube       every year on one 0.5 degree grid, in one file
    series     the longitudinal half — curves over time, areas, gross flows
    automata   the cellular automaton, calibrated on observed transitions
    render     classified grids, PNGs and the binary payload the console eats

Nothing here needs GDAL or rasterio: numpy, Pillow and the standard library.
"""

from . import sources  # noqa: F401

__all__ = ['sources', 'remote', 'raster', 'cube', 'series', 'automata', 'render']
__version__ = '1.0.0'
