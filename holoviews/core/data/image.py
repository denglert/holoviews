import numpy as np

from ..boundingregion import BoundingRegion, BoundingBox
from ..dimension import Dimension
from ..element import Element
from ..ndmapping import OrderedDict, NdMapping, item_check
from ..sheetcoords import SheetCoordinateSystem, Slice
from .. import util
from .grid import GridInterface
from .interface import Interface


class ImageInterface(GridInterface):
    """
    Interface for 2 or 3D arrays representing images
    of raw luminance values, RGB values or HSV values.
    """

    types = (np.ndarray,)

    datatype = 'image'

    @classmethod
    def init(cls, eltype, data, kdims, vdims):
        if kdims is None:
            kdims = eltype.kdims
        if vdims is None:
            vdims = eltype.vdims

        kwargs = {}
        dimensions = [d.alias if isinstance(d, Dimension) else
                      d for d in kdims + vdims]
        if isinstance(data, tuple):
            data = dict(zip(dimensions, data))
        if isinstance(data, dict):
            l, r, xdensity = util.bound_range(np.asarray(data[kdims[0].alias]), None)
            b, t, ydensity = util.bound_range(np.asarray(data[kdims[1].alias]), None)
            kwargs['xdensity'] = xdensity
            kwargs['ydensity'] = ydensity
            kwargs['bounds'] = BoundingBox(points=((l, b), (r, t)))
            if len(vdims) == 1:
                data = np.asarray(data[vdims[0].alias])
            else:
                data = np.dstack([data[vd.alias] for vd in vdims])
        if not isinstance(data, np.ndarray) or data.ndim not in [2, 3]:
            raise ValueError('ImageInterface expects a 2D array.')

        return data, {'kdims':kdims, 'vdims':vdims}, {}


    @classmethod
    def shape(cls, dataset):
        return dataset.data.shape

    @classmethod
    def validate(cls, dataset):
        pass

    @classmethod
    def redim(cls, dataset, dimensions):
        return dataset.data

    @classmethod
    def reindex(cls, columns, kdims=None, vdims=None):
        return columns.data

    @classmethod
    def range(cls, obj, dim):
        dim_idx = obj.get_dimension_index(dim)
        if dim_idx in [0, 1] and obj.bounds:
            l, b, r, t = obj.bounds.lbrt()
            if dim_idx:
                drange = (b, t)
            else:
                drange = (l, r)
        elif 1 < dim_idx < len(obj.vdims) + 2:
            dim_idx -= 2
            data = np.atleast_3d(obj.data)[:, :, dim_idx]
            drange = (np.nanmin(data), np.nanmax(data))
        else:
            drange = (None, None)
        return drange

    
    @classmethod
    def values(cls, dataset, dim, expanded=True, flat=True):
        """
        The set of samples available along a particular dimension.
        """
        dim_idx = dataset.get_dimension_index(dim)
        if dim_idx in [0, 1]:
            l, b, r, t = dataset.bounds.lbrt()
            dim2, dim1 = dataset.data.shape[:2]
            d1_half_unit = (r - l)/dim1/2.
            d2_half_unit = (t - b)/dim2/2.
            d1lin = np.linspace(l+d1_half_unit, r-d1_half_unit, dim1)
            d2lin = np.linspace(b+d2_half_unit, t-d2_half_unit, dim2)
            if expanded:
                values = np.meshgrid(d2lin, d1lin)[abs(dim_idx-1)]
                return values.flatten() if flat else values
            else:
                return d2lin if dim_idx else d1lin
        elif dim_idx == 2:
            # Raster arrays are stored with different orientation
            # than expanded column format, reorient before expanding
            data = np.flipud(dataset.data)
            return data.flatten() if flat else data
        else:
            return None, None


    @classmethod
    def select(cls, dataset, selection_mask=None, **selection):
        """
        Slice the underlying numpy array in sheet coordinates.
        """
        selection = {k: slice(*sel) if isinstance(sel, tuple) else sel
                     for k, sel in selection.items()}
        coords = tuple(selection[kd.alias] if kd.alias in selection else slice(None)
                       for kd in dataset.kdims)
        if not any([isinstance(el, slice) for el in coords]):
            return dataset.data[dataset.sheet2matrixidx(*coords)], {}
        xidx, yidx = coords
        l, b, r, t = dataset.bounds.lbrt()
        xunit = (1./dataset.xdensity)
        yunit = (1./dataset.ydensity)    
        if isinstance(xidx, slice):
            l = l if xidx.start is None else max(l, xidx.start)
            r = r if xidx.stop is None else min(r, xidx.stop)
        if isinstance(yidx, slice):
            b = b if yidx.start is None else max(b, yidx.start)
            t = t if yidx.stop is None else min(t, yidx.stop)
        bounds = BoundingBox(points=((l, b), (r, t)))
        slc = Slice(bounds, dataset)
        data = slc.submatrix(dataset.data)
        l, b, r, t = slc.compute_bounds(dataset).lbrt()
        if not isinstance(xidx, slice):
            xc, _ = dataset.closest_cell_center(xidx, b)
            l, r = xc-xunit/2, xc+xunit/2
            _, x = dataset.sheet2matrixidx(xidx, b)
            data = data[:, x][:, np.newaxis]
        elif not isinstance(yidx, slice):
            _, yc = dataset.closest_cell_center(l, yidx)
            b, t = yc-yunit/2, yc+yunit/2
            y, _ = dataset.sheet2matrixidx(l, yidx)
            data = data[y, :][np.newaxis, :]
        bounds = BoundingBox(points=((l, b), (r, t)))
        return data, {'bounds': bounds}


    @classmethod
    def length(cls, dataset):
        return np.product(dataset.data.shape)


    @classmethod
    def groupby(cls, dataset, dim_names, container_type, group_type, **kwargs):
        # Get dimensions information
        dimensions = [dataset.get_dimension(d) for d in dim_names]
        kdims = [kdim for kdim in dataset.kdims if kdim not in dimensions]

        # Update the kwargs appropriately for Element group types
        group_kwargs = {}
        group_type = dict if group_type == 'raw' else group_type
        if issubclass(group_type, Element):
            group_kwargs.update(util.get_param_values(dataset))
            group_kwargs['kdims'] = kdims
        group_kwargs.update(kwargs)

        if len(dimensions) == 1:
            didx = dataset.get_dimension_index(dimensions[0])
            coords = dataset.dimension_values(dimensions[0], False)
            xvals = dataset.dimension_values(abs(didx-1), False)
            samples = [(i, slice(None)) if didx else (slice(None), i)
                       for i in range(dataset.data.shape[abs(didx-1)])]
            if didx:
                samples = samples[::-1]
                data = dataset.data
            else:
                data = dataset.data[::-1, :]
            groups = [(c, group_type((xvals, data[s]), **group_kwargs))
                       for s, c in zip(samples, coords)]
        else:
            data = zip(*[dataset.dimension_values(i) for i in range(len(dataset.dimensions()))])
            groups = [(g[:dataset.ndims], group_type([g[dataset.ndims:]], **group_kwargs))
                      for g in data]

        if issubclass(container_type, NdMapping):
            with item_check(False):
                return container_type(groups, kdims=dimensions)
        else:
            return container_type(grouped_data)


    @classmethod
    def aggregate(cls, dataset, kdims, function, **kwargs):
        kdims = [kd.alias if isinstance(kd, Dimension) else kd for kd in kdims]
        axes = tuple(dataset.ndims-dataset.get_dimension_index(kdim)-1
                     for kdim in dataset.kdims if kdim not in kdims)
        
        data = np.atleast_1d(function(dataset.data, axis=axes, **kwargs))
        if np.isscalar(data):
            return data
        elif len(axes) == 1:
            return {kdims[0]: cls.values(dataset, axes[0], expanded=False),
                    dataset.vdims[0].alias: data}


Interface.register(ImageInterface)
