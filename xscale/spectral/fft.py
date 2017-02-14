# Xarray
import xarray as xr
# Numpy
import numpy as np
from numpy.compat import integer_types
from numpy.core import integer

# Dask
import dask.array as da
# Internals
import copy
# Xscale
from .. import _utils
# Warnings
import warnings

integer_types = integer_types + (integer,)

import pytest

def ps(array, nfft=None, dim=None, dx=None, detrend=None, tapering=False,
       shift=True, chunks=None):
	"""
	Compute the Power Spectrum (PS)

	Parameters
	----------
	array : xarray.DataArray
		Array from which compute the spectrum
	dim : str or sequence, optional
		Dimensions along which to compute the spectrum
	dx : float or sequence, optional
		Define the resolution of the dimensions. If not precised,
		the resolution is computed directly from the coordinates associated
		to the dimensions.
	detrend : {None, 'zeromean', 'linear'}, optional
		Remove the mean or a linear trend before the spectrum computation
	tapering : bool, optional
		If True, tapper the data with a Tukey window
	shift : bool, optional
		If True, the frequency axes are centered around 0.
    chunks : int, tuple or dict, optional
	    Chunk sizes along each dimension, e.g., ``5``, ``(5, 5)`` or
	    ``{'x': 5, 'y': 5}``

	Returns
	-------
	spectrum : xarray.DataArray
		Spectral array computed over the different arrays
	"""
	spec = fft(array, nfft=nfft, dim=dim, dx=dx, detrend=detrend,
	           tapering=tapering, shift=shift, chunks=chunks)
	power_spectrum = (spec * da.conj(spec)).real * spec.attrs['ps_factor']
	#pytest.set_trace()
	if array.name is None:
		power_spectrum.name = 'PS'
	else:
		power_spectrum.name = 'PS_' + array.name
	power_spectrum.attrs['description'] = ('Power Spectrum (PS) performed '
	                                       'along dimension(s) %s ' % dim)
	return power_spectrum


def psd(array, nfft=None, dim=None, dx=None, detrend=None, tapering=False,
        shift=True, chunks=None):
	"""Compute the Power Spectrum Density (PSD)

	Parameters
	----------
	array : xarray.DataArray
		Array from which compute the spectrum
	dim : str or sequence, optional
		Dimensions along which to compute the spectrum
	dx : float or sequence, optional
		Define the resolution of the dimensions. If not precised,
		the resolution is computed directly from the coordinates associated
		to the dimensions.
	detrend : {None, 'zeromean', 'linear'}, optional
		Remove the mean or a linear trend before the spectrum computation
	tapering : bool, optional
		If True, tapper the data with a Tukey window
    chunks : int, tuple or dict, optional
	    Chunk sizes along each dimension, e.g., ``5``, ``(5, 5)`` or
	    ``{'x': 5, 'y': 5}``

	Returns
	-------
	spectrum : xarray.DataArray
		Spectral array computed over the different arrays
	"""
	spec = fft(array, nfft=nfft, dim=dim, dx=dx, detrend=detrend,
	           tapering=tapering, shift=shift, chunks=chunks)
	#TODO: Make the correct normalization for the power spectrum and check with the Parseval theorem
	power_spectrum_density = ((spec * da.conj(spec)).real *
	                          spec.attrs['psd_factor'])
	if array.name is None:
		power_spectrum_density.name = 'PSD'
	else:
		power_spectrum_density.name = 'PSD_' + array.name
	power_spectrum_density.attrs['description'] = ('Power Spectrum Density '
	                                               '(PSD) performed along '
	                                               'dimension(s) %s ' % dim)
	return power_spectrum_density


def fft(array, nfft=None, dim=None, dx=None, detrend=None, tapering=False,
        shift=False, chunks=None):
	"""
	Compute the spectrum on several dimensions of xarray.DataArray objects using
	 the Fast Fourrier Transform parrallelized with dask.

	Parameters
	----------
	array : xarray.DataArray
		Array from which compute the spectrum
	dim : str or sequence
		Dimensions along which to compute the spectrum
	dx : float or sequence, optional
		Define the resolution of the dimensions. If not precised,
		the resolution is computed directly from the coordinates associated
		to the dimensions.
	detrend : {None, 'zeromean', 'linear'}, optional
		Remove the mean or a linear trend before the spectrum computation
	tapering : bool, optional
		If True, tapper the data with a Tukey window
	chunks : int, tuple or dict, optional
		Chunk sizes along each dimension, e.g., ``5``, ``(5, 5)`` or
		``{'x': 5, 'y': 5}``

	Returns
	-------
	res :
		A new

	Notes
	-----
	If the input data is real, a real fft is performed over the first
	dimension, which is faster. Then the transform over the remaining
	dimensions are computed with the classic fft.
	"""
	temp_nfft, new_dim = _utils.infer_n_and_dims(array, nfft, dim)
	new_nfft = _utils.infer_arg(temp_nfft, dim)
	new_dx = _utils.infer_arg(dx, dim)
	if detrend is 'zeromean':
		# Tackling the issue of the dask graph by computing and loading the
		# mean here
		mean_array = array.mean(dim=new_dim).load()
		preproc_array = array - mean_array
	elif detrend is 'linear':
		preproc_array = _detrend(array, new_dim)
	else:
		preproc_array = array
	if tapering:
		preproc_array = _tapper(array, new_dim)
	spectrum_array, spectrum_coords, spectrum_dims = \
		_fft(preproc_array, new_nfft, new_dim, new_dx, shift=shift,
		     chunks=chunks)
	spec = xr.DataArray(spectrum_array, coords=spectrum_coords,
	                    dims=spectrum_dims, name='spectrum')
	_compute_norm_factor(spec, new_nfft, new_dim, tapering)
	return spec


def _fft(array, nfft, dim, dx, shift=False, chunks=None):
	"""This function is for private use only.
	"""
	spectrum_array = array.chunk(chunks=chunks).data
	spectrum_coords = dict()
	spectrum_dims = tuple()
	for di in array.dims:
		if di not in dim:
			spectrum_dims += (di,)
			spectrum_coords[di] = np.asarray(array[di])
		else:
			spectrum_dims += ('f_' + di,)
	chunks = copy.copy(spectrum_array.chunks)
	first = True
	for di in dim:
		if di in array.dims:
			axis_num = array.get_axis_num(di)
			dim_length = array.shape[axis_num]
			# Compute the resolution of the different dimension
			if dx[di] is None:
				dx[di] = _utils.get_dx(array, di)
			#FFT part
			if first and not np.iscomplexobj(spectrum_array):
				# The first FFT is performed on real numbers: the use of rfft is faster
				spectrum_coords['f_' + di] = np.fft.rfftfreq(nfft[di], dx[di])
				spectrum_array = \
					(da.fft.rfft(spectrum_array.rechunk({axis_num: dim_length}),
					             axis=axis_num).
					 rechunk({axis_num: chunks[axis_num][0]}))
			else:
				# The successive FFTs are performed on complex numbers: need to use classic fft
				spectrum_coords['f_' + di] = np.fft.fftfreq(nfft[di], dx[di])
				spectrum_array = \
					(da.fft.fft(spectrum_array.rechunk({axis_num: dim_length}),
					            axis=axis_num).
				    rechunk({axis_num: chunks[axis_num][0]}))
				if shift is True:
					spectrum_coords['f_' + di] = \
						np.fft.fftshift(spectrum_coords['f_' + di])
					#TODO: np.fft.fftshift impose to compute the dask graph !
					spectrum_array = _fftshift(spectrum_array, axes=axis_num)
			first = False
		else:
			warnings.warn("Cannot find dimension %s in DataArray" % di)
	return spectrum_array, spectrum_coords, spectrum_dims


def _detrend(array, dim):
	# TODO: implement the detrending function
	raise NotImplementedError("The linear detrending option is not implemented "
	                          "yet.")


def _tapper(array, dim):
	"""Perform a tappering of the data over the specified dimensions with a tukey window
	"""
	# TODO: implement the tapering function
	raise NotImplementedError("The tapering option is not implemented yet.")


def _compute_norm_factor(array, nfft, dim, tapering):
	"""Compute the normalization factor for Power Spectrum and Power Spectrum Density
	"""
	try:
		ps_factor = array.attrs['ps_factor']
	except:
		ps_factor = 1.
	try:
		psd_factor = array.attrs['psd_factor']
	except:
		psd_factor = 1.
	for di in dim:
		if tapering:
			raise NotImplementedError("The tapering option is not implemented "
			                          "yet.")
		else:
			df = np.diff(array['f_' + di])[0]
			s1 = nfft[di]
			s2 = s1
		ps_factor /= s1 ** 2
		psd_factor /= df * s2
	array.attrs['ps_factor'] = ps_factor
	array.attrs['psd_factor'] = psd_factor


def _fftshift(x, axes=None):
    if axes is None:
        axes = list(range(x.ndim))
    elif isinstance(axes, integer_types):
        axes = (axes,)
    for k in axes:
        n = x.shape[k]
        p2 = (n + 1) // 2
        mylist = np.concatenate((np.arange(p2, n), np.arange(p2)))
        x = da.take(x, mylist, k)
    return x


def _ifftshift(x, axes=None):
    if axes is None:
        axes = list(range(x.ndim))
    elif isinstance(axes, integer_types):
        axes = (axes,)
    for k in axes:
        n = x.shape[k]
        p2 = n - (n + 1) // 2
        mylist = np.concatenate((np.arange(p2, n), np.arange(p2)))
        x = da.take(x, mylist, k)
	return x