# Python 2/3 compatibility
from __future__ import absolute_import, division, print_function
# Numpy
import numpy as np
# Xarray
import xarray as xr
# Dask
import dask.array as da
# Pandas
import pandas as pd
# Xscale
from .. import _utils


def polyfit(array, dim, deg=1):
	"""
	Least squares polynomial fit.
	Fit a polynomial ``p(x) = p[deg] * x ** deg + ... + p[0]`` of degree `deg`
	Returns a vector of coefficients `p` that minimises the squared error.

	Parameters
	----------
	x : xarray.DataArray
		The array to fit
	dim : str
		Dimension along which the fit is performed
	deg : int
		Degree of the fitting polynomial


	Returns
	-------
	output : xarray.DataArray
		Polynomial coefficients
	"""
	if dim is None:
		dim = array.dims[0]
	if _utils.is_scalar(periods):
		periods = [periods, ]
	n = 2 * len(periods) + 1
	# Sort frequencies in ascending order
	periods.sort(reverse=True)
	# Re-order the array to place the fitting dimension as the first dimension
	# + stack the other dimensions
	array_stacked = _order_and_stack(array, dim)
	dim_chunk = array.chunks[array.get_axis_num(dim)][0]
	stacked_dims = [di for di in array.dims if di is not dim]
	new_dims = [dim, ] + stacked_dims
	stacked_array = array.transpose(*new_dims).stack(temp_dim=stacked_dims)
	dim_chunk = array.chunks[array.get_axis_num(dim)][0]
	# Build coefficient matrix for the fit
	x = da.vstack([array[dim].data ** d for d in range(deg + 1)]).T
	x = x.rechunk((dim_chunk, deg + 1))
	# Solve the least-square system
	p, err, _, _ = da.linalg.lstsq(x, stacked_array.data)
	# TO DO: Compute and store the errors associated to the fit
	# Store the result in a DataArray object
	new_dims = ('degree',) + stacked_array.dims[1:]
	ds = xr.DataArray(p, name='polynomial_coefficients',
	                  coords=stacked_array.coords, dims=new_dims)
	return ds.unstack('temp_dim').assign_coords(degree=range(deg + 1))


def polyval(coefficients, coord):
	"""
	Build an array from polynomial coefficients

	Parameters
	----------
	coefficients : xarray.DataArray
		The DataArray where the coefficients are stored
	coord : xarray.Coordinate
		The locations where the polynomials is evaluated

	Returns
	-------
	output : xarray.DataArray
		The polynomials evaluated at specified locations
	"""
	#TODO
	raise NotImplementedError


def sinfit(array, periods, dim=None, coord=None, unit='s'):
	"""
	Least squares sinusoidal fit.
	Fit sinusoidal functions ``y = A[p] * sin(2 * pi * ax * f[1] + phi[1])``

	Parameters
	----------
	array : xarray.DataArray
		Data to be fitted
	periods: float or list of float
		The periods of the sinusoidal functions to be fitted
	dim : str, optional
		The dimension along which the data will be fitted. If not precised,
		the first dimension will be used
	unit : {'D', 'h', 'm', 's', 'ms', 'us', 'ns'}, optional
		If the fit uses a datetime dimension, the unit of the period may be
		specified here.

	Returns
	-------
	modes : Dataset
		A Dataset with the amplitude and the phase for each periods
	"""
	if dim is None:
		dim = array.dims[0]
	if _utils.is_scalar(periods):
		periods = [periods, ]
	n = 2 * len(periods) + 1
	# Sort frequencies in ascending order
	periods.sort(reverse=True)
	# Re-order the array to place the fitting dimension as the first dimension
	# + stack the other dimensions
	array_stacked = _order_and_stack(array, dim)
	dim_chunk = array.chunks[array.get_axis_num(dim)][0]
	# Check if the dimension is associated with a numpy.datetime
	# and normalize to use periods and time in seconds
	if coord is None:
		coord = array[dim]
	if pd.core.common.is_datetime64_dtype(coord.data):
		# Use the 1e-9 to scale nanoseconds to seconds (by default, xarray use
		# datetime in nanoseconds
		t = coord.data.astype('f8') * 1e-9
		freqs = 1. / pd.to_timedelta(periods, unit=unit).total_seconds()
	else:
		t = coord
		freqs = 1. / periods
	# Build coefficient matrix for the fit using the exponential form
	x = da.vstack([da.cos(2 * np.pi * f * t) for f in reversed(freqs)] +
	              [da.ones(len(t), chunks=dim_chunk), ] +
	              [da.sin(2 * np.pi * f * t) for f in freqs]).T
	x = x.rechunk((dim_chunk, n))
	# Solve the least-square system
	c, _, _, _ = da.linalg.lstsq(x, array_stacked.data)
	# Get cosine (a) and sine (b) ampitudes
	b = c[0:n//2, ][::-1]
	a = c[n//2 + 1:, ]
	# Compute amplitude and phase
	amplitude = da.sqrt(a ** 2 + b ** 2)
	phase = da.arctan2(b, a) * 180. / np.pi
	# Store the results
	new_dims = ('periods',) + array_stacked.dims[1:]
	new_coords = {co: array_stacked.coords[co] for co in array_stacked.coords
	              if co is not dim}
	var_dict = {'amplitude': (new_dims, amplitude),
	            'phase': (new_dims, phase),
	            'offset': (array_stacked.dims[1:], c[n//2, ])}
	ds = xr.Dataset(var_dict, coords=new_coords)
	ds = ds.assign_coords(periods=periods)
	ds['periods'].attrs['units'] = unit
	# Unstack the data
	modes = _unstack(ds)
	return modes


def sinval(modes, coord):
	"""
	Evaluate a sinusoidal function based on a modal decomposition. Each mode is
	defined by a period, an amplitude and a phase.

	Parameters
	----------
	modes : xarray.Dataset
		A dataset where the amplitude and phase are stored for each mode
	coord : xarray.Coordinates
		A coordinate array at which the sine functions are evaluated

	Returns
	-------
	res : xarray.DataArray

	"""
	modes_dims = tuple([di for di in modes.dims if di is not 'periods'])
	modes_shape = tuple([modes.dims[di] for di in modes_dims])
	modes_chunks = tuple(modes.chunks[di][0] for di in modes_dims)
	if coord.chunks is  None:
		coord_chunks = (coord.shape[0],)
	else:
		coord_chunks = (coord.chunks[0][0],)
	new_dims = coord.dims + modes_dims
	new_shape = coord.shape + modes_shape
	new_chunks = coord_chunks + modes_chunks
	ones = xr.DataArray(da.ones(new_shape, chunks=new_chunks), dims=new_dims)
	if pd.core.common.is_datetime64_dtype(coord):
		# TODO: Check if there is a smarter way to convert time to second
		t = ones * coord.astype('f8') * 1e-9
		pd_periods = pd.to_datetime(modes['periods'],
		                            unit=modes['periods'].units)
		if _utils.is_scalar(modes['periods'].data):
			periods = pd_periods.value.astype('f8') * 1e-9
		else:
			periods = pd_periods.values.astype('f8') * 1e-9
	else:
		t = ones * coord
		periods = modes['periods']
	res = ones * modes['offset']
	for p in range(len(periods)):
		modep = ones * modes.isel(periods=p)
		res += modep['amplitude'] * xr.ufuncs.sin(2 * np.pi * t / periods[p] +
		                                          modep['phase'] * np.pi / 180.)
	return res


def detrend(array, dim=None, typ='linear', chunks=None):
	"""
	Remove the mean, linear or quadratic trend and remove it.

	Parameters
	----------
	array : xarray.DataArray
		DataArray that needs to be detrended along t
	dim:
		Dimension over which the array will be detrended
	"""
	raise NotImplementedError


def _order_and_stack(obj, dim):
	"""
	Private function used to reorder to use the work dimension as the first
	dimension, stack all the dimensions except the first one
	"""
	dims_stacked = [di for di in obj.dims if di is not dim]
	new_dims = [dim, ] + dims_stacked
	if obj.ndim > 2:
		obj_stacked = obj.transpose(*new_dims).stack(temp_dim=dims_stacked)
	elif obj.ndim == 2:
		obj_stacked = obj.transpose(*new_dims)
	else:
		obj_stacked = obj
	return obj_stacked


def _unstack(obj):
	"""
	Private function used to reorder to use the work dimension as the first
	dimension, stack all the dimensions except the first one
	"""
	if 'temp_dim' in obj.dims:
		obj_unstacked = obj.unstack('temp_dim')
	else:
		obj_unstacked = obj
	return obj_unstacked
