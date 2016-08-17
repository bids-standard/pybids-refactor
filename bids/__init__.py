from __future__ import absolute_import, division, print_function
from .version import __version__  # noqa
from . import *  # noqa
from .due import due, Doi

due.cite(Doi("10.1038/sdata.2016.44"),
         description="Brain Imaging Data Structure",
         tags=["reference-implementation"],
         path='bids')
