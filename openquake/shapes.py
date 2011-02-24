# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 softtabstop=4
"""
Collection of base classes for processing 
spatially-related data."""

import math

import geohash
import json

from numpy import zeros # pylint: disable=F0401,E0611
from numpy import empty # pylint: disable=F0401,E0611
from numpy import allclose # pylint: disable=F0401,E0611

from shapely import wkt
from shapely import geometry
from scipy.interpolate import interp1d # pylint: disable=F0401,E0611

from openquake import flags
from openquake import java

FLAGS = flags.FLAGS

flags.DEFINE_integer('distance_precision', 12, 
    "Points within this geohash precision will be considered the same point")

LineString = geometry.LineString # pylint: disable=C0103
Point = geometry.Point           # pylint: disable=C0103
COORD_PRECISION = 7
INTUP = 10**COORD_PRECISION

class Region(object):
    """A container of polygons, used for bounds checking"""
    def __init__(self, polygon):
        self._grid = None
        self.polygon = polygon
        self.cell_size = 0.1
        # TODO(JMC): Make this a multipolygon instead?

    def set_cell_size(self, cell_size):
        self.cell_size = cell_size * INTUP
        
    @classmethod
    def from_coordinates(cls, coordinates):
        """Build a region from a set of coordinates"""
        coordinates = [(int(INTUP * x), int(INTUP * y)) for (x,y) in coordinates]
        polygon = geometry.Polygon(coordinates)
        return cls(polygon)
    
    @classmethod
    def from_simple(cls, top_left, bottom_right):
        """Build a region from two corners (top left, bottom right)"""
        points = [top_left,
                  (top_left[0], bottom_right[1]),
                  bottom_right,
                  (bottom_right[0], top_left[1])]
        return cls.from_coordinates(points)
    
    @classmethod
    def from_file(cls, path):
        """Load a region from a wkt file with a single polygon"""
        with open(path) as wkt_file:
            polygon = wkt.loads(wkt_file.read())
            return cls(polygon=polygon)
    
    @property
    def bounds(self):
        """Returns a bounding box containing the whole region"""
        return self.polygon.bounds
    
    @property
    def lower_left_corner(self):
        """Lower left corner of the containing bounding box"""
        (minx, miny, _maxx, _maxy) = self.bounds
        return Site.integer_site(minx, miny)
        
    @property
    def lower_right_corner(self):
        """Lower right corner of the containing bounding box"""
        (_minx, miny, maxx, _maxy) = self.bounds
        return Site.integer_site(maxx, miny)
            
    @property
    def upper_left_corner(self):
        """Upper left corner of the containing bounding box"""
        (minx, _miny, _maxx, maxy) = self.bounds
        return Site.integer_site(minx, maxy)
        
    @property
    def upper_right_corner(self):
        """Upper right corner of the containing bounding box"""
        (_minx, _miny, maxx, maxy) = self.bounds
        return Site.integer_site(maxx, maxy)
    
    @property
    def grid(self):
        """Returns a proxy interface that maps lat/lon 
        to col/row based on a specific cellsize. Proxy is
        also iterable."""
        if not self._grid:
            if not self.cell_size:
                raise Exception(
                    "Can't generate grid without cell_size being set")
            self._grid = Grid(self, self.cell_size * INTUP)
        return self._grid
    
    @property
    def sites(self):
        """ Returns a list of sites created from iterating over self """
        sites = []
        
        for site in self:
            sites.append(site)
        
        return sites
    
    def __iter__(self):    
        if not self.cell_size:
            raise Exception(
                "Can't generate grid without cell_size being set")
        for gridpoint in self.grid:
            yield gridpoint.site


class RegionConstraint(Region):
    """Extends a basic region to work as a constraint on parsers"""
    def match(self, point):
        """Point (specified by Point class or tuple) is contained?"""
        if isinstance(point, Site):
            point = point.point
        if not isinstance(point, geometry.Point): 
            point = geometry.Point(int(point[0]*INTUP), int(point[1]*INTUP))
        test = self.polygon.contains(point) or self.polygon.touches(point)
        return test


class GridPoint(object):
    """Simple (trivial) point class"""
    def __init__(self, grid, column, row):
        self.column = column
        self.row = row
        self.grid = grid
    
    def __eq__(self, other):
        if isinstance(other, Site):
            other = self.grid.point_at(other)
        test = (self.column == other.column 
                and self.row == other.row 
                and self.grid == other.grid)
        return test
    
    @property
    def site(self):
        """Trivial accessor for Site at Grid point"""
        return self.grid.site_at(self)

    def hash(self):
        """Ugly hashing function
        TODO(jmc): Fixme"""
        return self.__hash__()
        
    def __repr__(self):
        return str(self.__hash__())

    def __hash__(self):
        return self.column * 1000000000 + self.row 
        #, int(self.grid.cell_size)

    def __str__(self):
        return self.__repr__()

class BoundsException(Exception):
    """Point is outside of region"""
    pass


class Grid(object):
    """Grid is a proxy interface to Region, which translates
    lat/lon to col/row"""
    
    def __init__(self, region, cell_size=0.1*INTUP):
        self.region = region
        self.cell_size = cell_size
        self.lower_left_corner = self.region.lower_left_corner
        self.columns = self._longitude_to_column(
                    self.region.upper_right_corner._longitude) + 1
        self.rows = self._latitude_to_row(
                    self.region.upper_right_corner._latitude) + 1

    def check_site(self, site):
        """Confirm that the site is contained by the region"""
        return self.check_point(site.point)
    
    def check_point(self, point):    
        """ Confirm that the point is within the polygon 
        underlying the gridded region"""
        if (self.region.polygon.contains(point)):
            return True
        if self.region.polygon.touches(point):
            return True
        raise BoundsException("Point is not on the Grid")
    
    def check_gridpoint(self, gridpoint):
        """Confirm that the point is contained by the region"""
        point = Point(self._column_to_longitude(gridpoint.column),
                             self._row_to_latitude(gridpoint.row))
        return self.check_point(point)
    
    def _latitude_to_row(self, latitude):
        """Calculate row from latitude value"""
        latitude_offset = math.fabs(latitude - self.lower_left_corner._latitude)
        return int(round(latitude_offset / self.cell_size))

    def _row_to_latitude(self, row):
        """Determine latitude from given grid row"""
        return self.lower_left_corner._latitude + ((row) * self.cell_size)

    def _longitude_to_column(self, longitude):
        """Calculate column from longitude value"""
        longitude_offset = longitude - self.lower_left_corner._longitude
        return int(round(longitude_offset / self.cell_size))
    
    def _column_to_longitude(self, column):
        """Determine longitude from given grid column"""
        return self.lower_left_corner._longitude + ((column) * self.cell_size)

    def point_at(self, site):
        """Translates a site into a matrix bidimensional point."""
        self.check_site(site)
        row = self._latitude_to_row(site._latitude)
        column = self._longitude_to_column(site._longitude)
        return GridPoint(self, column, row)
    
    def site_at(self, gridpoint):    
        """Construct a site at the given grid point"""
        return Site.integer_site(self._column_to_longitude(gridpoint.column),
                             self._row_to_latitude(gridpoint.row))
    def __iter__(self):
        for row in range(0, self.rows):
            for col in range(0, self.columns):
                try:
                    point = GridPoint(self, col, row)
                    self.check_gridpoint(point)
                    yield point
                except BoundsException:
                    print "GACK! at col %s row %s" % (col, row)
                    print "Point at %s %s isnt on grid" % \
                        (point.site._longitude, point.site._latitude)

def c_mul(val_a, val_b):
    """Ugly method of hashing string to integer
    TODO(jmc): Get rid of points as dict keys!"""
    return eval(hex((long(val_a) * val_b) & 0xFFFFFFFFL)[:-1])


class Site(object):
    """Site is a dictionary-keyable point"""
    
    def __init__(self, longitude, latitude):
        self.point = geometry.Point(int(longitude*INTUP), int(latitude*INTUP))
        
    @classmethod
    def integer_site(cls, _longitude, _latitude):
        return cls(_longitude / INTUP, _latitude / INTUP)
    
    @property
    def coords(self):
        """Return a tuple with the coordinates of this point"""
        return (self.longitude, self.latitude)

    @property
    def longitude(self):
        """Point x value is longitude"""
        return self.point.x / INTUP
        
    @property
    def _longitude(self):
        """Point x value is longitude"""
        return self.point.x 

    @property
    def latitude(self):
        """Point y value is latitude"""
        return self.point.y / INTUP
        
    @property
    def _latitude(self):
        """Point y value is latitude"""
        return self.point.y

    def __eq__(self, other):
        return self.hash() == other.hash()
    
    def __ne__(self, other):
        return not self == other

    def equals(self, other):
        """Verbose wrapper around =="""
        if isinstance(other, Site):
            other = other.point
        return self.point.equals(other)

    def hash(self):
        """Ugly geohashing function, get rid of this!
        TODO(jmc): Dont use sites as dict keys"""
        return self._geohash()

    def __hash__(self):
        if not self:
            return 0 # empty
        geohash_val = self._geohash()
        value = ord(geohash_val[0]) << 7
        for char in geohash_val:
            value = c_mul(1000003, value) ^ ord(char)
        value = value ^ len(geohash_val)
        if value == -1:
            value = -2
        return value
    
    def to_java(self):
        """Converts to a Java Site object"""
        jpype = java.jvm()
        loc_class = jpype.JClass("org.opensha.commons.geo.Location")
        site_class = jpype.JClass("org.opensha.commons.data.Site")
        # TODO(JMC): Support named sites?
        return site_class(loc_class(self.latitude, self.longitude))
    
    def _geohash(self):
        """A geohash-encoded string for dict keys"""
        return geohash.encode(self.latitude, self.longitude, 
            precision=FLAGS.distance_precision)
    
    def __cmp__(self, other):
        return self.hash() == other.hash()
    
    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return "<Site(%s, %s)>" % (self.longitude, self.latitude)


class Field(object):
    """Uses a 2 dimensional numpy array to store a field of values."""
    def __init__(self, field=None, rows=1, cols=1):
        if field is not None:
            self.field = field
        else:
            self.field = zeros((rows, cols))
    
    def get(self, row, col):
        """ Return the value at self.field[row][col] 
            :param row
            :param col
        """
        try:
            return self.field[row][col]
        except IndexError:
            print "Field with shape [%s] doesn't have value at [%s][%s]" % (
                self.field.shape, row, col)
    
    @classmethod
    def from_json(cls, json_str, grid=None):
        """Construct a field from a serialized version in
        json format."""
        assert grid
        as_dict = json.JSONDecoder().decode(json_str)
        return cls.from_dict(as_dict, grid=grid)
    
    @classmethod
    def from_dict(cls, values, transform=math.exp, grid=None):
        """Construct a field from a dictionary.
        """
        assert grid
        assert grid.cell_size
        field = zeros((grid.rows, grid.columns))
        
        for key, field_site in values.items(): #pylint: disable=W0612
            point = grid.point_at(
                Site(field_site['lon'], field_site['lat']))
            field[point.row][point.column] = transform(float(field_site['mag']))

        return cls(field)   

class FieldSet(object):
    """ An iterator for a set of fields """
    
    def __init__(self, as_dict, grid):
        assert grid
        self.grid = grid
        self.fields = as_dict.values()[0] # NOTE: There's a junk wrapper
    
    @classmethod
    def from_json(cls, json_str, grid=None):
        """ Construct a field set from a serialized version in json format """
        assert grid
        as_dict = json.JSONDecoder().decode(json_str)
        return cls(as_dict, grid=grid)
    
    def __iter__(self):
        """Pop off the fields sequentially"""
        for field in self.fields.values():
            yield Field.from_dict(field, grid=self.grid)


class Curve(object):
    """This class defines a curve (discrete function)
    used in the risk domain."""

    @classmethod
    def from_json(cls, json_str):
        """Construct a curve from a serialized version in
        json format."""
        as_dict = json.JSONDecoder().decode(json_str)
        return cls.from_dict(as_dict)

    @classmethod
    def from_dict(cls, values):
        """Construct a curve from a dictionary.
        
        The dictionary keys can be unordered and of
        whatever type can be converted to float with float().
        """

        data = []
        
        for key, val in values.items():
            data.append((float(key), val))

        return cls(data)

    def __init__(self, values):
        """Construct a curve from a sequence of tuples.
        
        The value on the first position of the tuple is the x value,
        the value(s) on the second position is the y value(s). This class
        supports multiple y values for the same x value, for example:
        
        Curve([(0.1, 1.0), (0.2, 2.0)]) # single y value
        Curve([(0.1, (1.0, 0.5)), (0.2, (2.0, 0.5))]) # multiple y values
        
        You can also pass lists instead of tuples, like:

        Curve([(0.1, [1.0, 0.5]), (0.2, [2.0, 0.5])])
        """

        # sort the values on x axis
        values = sorted(values, key=lambda data: data[0])
        
        elements = len(values)
        self.x_values = empty(elements)
        self.y_values = empty(elements)

        if elements and type(values[0][1]) in (tuple, list):
            self.y_values = empty((elements, len(values[0][1])))

        for index, (key, val) in enumerate(values):
            self.x_values[index] = key
            self.y_values[index] = val

    def __eq__(self, other):
        return allclose(self.x_values, other.x_values) \
                and allclose(self.y_values, other.y_values)

    def __str__(self):
        return "X Values: %s\nY Values: %s" % (
                self.x_values.__str__(), self.y_values.__str__())

    def rescale_abscissae(self, value) :
        """Return a new curve with each abscissa value multiplied
        by the value passed as parameter."""
        
        result = Curve(())
        result.x_values = self.x_values * value
        result.y_values = self.y_values
        
        return result

    @property
    def abscissae(self):
        """Return the abscissa values of this curve in ascending order."""
        return self.x_values

    @property
    def is_empty(self):
        """Return true if this curve is empty, false otherwise."""
        return self.abscissae.size == 0

    @property
    def ordinates(self):
        """Return the ordinate values of this curve in ascending order
        of the corresponding abscissa values."""
        return self.y_values
    
    @property
    def is_multi_value(self):
        """Return true if this curve describes multiple ordinate values,
        false otherwise."""
        return self.y_values.ndim > 1

    def ordinate_for(self, x_value, y_index=0):
        """Return the y value corresponding to the given x value."""
        
        y_values = self.y_values
        
        if self.y_values.ndim > 1:
            y_values = self.y_values[:, y_index]
        
        return interp1d(self.x_values, y_values)(x_value)

    def abscissa_for(self, y_value):
        """Return the x value corresponding to the given y value."""

        data = []
        # inverting the function
        for x_value in self.abscissae:
            data.append((self.ordinate_for(x_value), x_value))
        
        return Curve(data).ordinate_for(y_value)

    def ordinate_out_of_bounds(self, y_value):
        """Check if the given value is outside the Y values boundaries."""
        ordinates = list(self.ordinates)
        ordinates.sort()
        
        return y_value < ordinates[0] or y_value > ordinates[-1]

    def to_json(self):
        """Serialize this curve in json format."""
        as_dict = {}
        
        for index, x_value in enumerate(self.x_values):
            if self.y_values.ndim > 1:
                as_dict[str(x_value)] = list(self.y_values[index])
            else:
                as_dict[str(x_value)] = self.y_values[index]

        return json.JSONEncoder().encode(as_dict)


class VulnerabilityFunction(Curve):
    """This class represents a vulnerability fuction.

    A vulnerability function has IMLs (Intensity Measure Levels) as
    X values and MLRs, CVs (Mean Loss Ratio and Coefficent of Variation)
    as Y values.
    """

    @property
    def means(self):
        """Return the mean loss ratios of this function."""
        if self.is_empty:
            return self.ordinates
        else:
            return self.ordinates[:, 0]

    def __iter__(self):
        """Iterate on the values of this function, returning triples
        in the form of (iml, mean loss ratio, cov)."""
        for idx in range(len(self.abscissae)):
            yield((self.abscissae[idx], 
                    self.ordinates[idx][0],
                    self.ordinates[idx][1]))

    @property
    def imls(self):
        """Return the imls of this function."""
        return self.abscissae

    @property
    def covs(self):
        """Return the covs of this function."""
        return self.ordinates[:, 1]


EMPTY_CURVE = Curve(())
EMPTY_VULN_FUNCTION = VulnerabilityFunction(())
