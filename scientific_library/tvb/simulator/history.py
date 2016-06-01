# -*- coding: utf-8 -*-
#
#
# TheVirtualBrain-Framework Package. This package holds all Data Management, and
# Web-UI helpful to run brain-simulations. To use it, you also need do download
# TheVirtualBrain-Scientific Package (for simulators). See content of the
# documentation-folder for more details. See also http://www.thevirtualbrain.org
#
# (c) 2012-2013, Baycrest Centre for Geriatric Care ("Baycrest")
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 2 as published by the Free
# Software Foundation. This program is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
# License for more details. You should have received a copy of the GNU General
# Public License along with this program; if not, you can download it here
# http://www.gnu.org/licenses/old-licenses/gpl-2.0
#
#
# CITATION:
# When using The Virtual Brain for scientific publications, please cite it as follows:
#
#   Paula Sanz Leon, Stuart A. Knock, M. Marmaduke Woodman, Lia Domide,
#   Jochen Mersmann, Anthony R. McIntosh, Viktor Jirsa (2013)
#       The Virtual Brain: a simulator of primate brain network dynamics.
#   Frontiers in Neuroinformatics (7:10. doi: 10.3389/fninf.2013.00010)
#
#

"""
Simulator history implementation.

For now it contains only functions to fetch from a history buffer.
In the future it makes sense to have classes that encapsulate the history buffer and querying strategies.

.. moduleauthor:: Mihai Andrei <mihai.andrei@codemart.ro>
.. moduleauthor:: Marmaduke Woodman <mmwoodman@gmail.com>

"""


import numpy
from tvb.simulator.common import get_logger
from .descriptors import StaticAttr, Dim, NDArray


LOG = get_logger(__name__)


try:
    import tvb._speedups.history as chist
    LOG.info('Using C speedups for history')

    def get_state(history, time_idx, cvar, node_ids, out):
        """
        Fetches a delayed state from history
        :param history: History array. (time, state_vars, nodes, modes)
        :param time_idx: Delay indices. (nodes, 1 nodes)
        :param cvar: Coupled vars indices. (1, ncvar, 1)
        :param: out: The delayed states (nodes, ncvar, nodes, modes)
        """
        chist.get_state(history, time_idx, cvar, out)

    def _get_state_mask(history, time_idx, cvar, conn_mask, out):
        """
        Fetches a delayed state from history. Uses a mask to avoid fetching history for uncoupled nodes. Faster than get_state
        :param history: History array. (time, state_vars, nodes, modes)
        :param time_idx: Delay indices. (nodes, 1 nodes)
        :param cvar: Coupled vars indices. (1, ncvar, 1)
        :param conn_mask: Should be 0 where the weights are 0 1 otherwise.(nodes, nodes)
        :param: out: The delayed states (nodes, ncvar, nodes, modes)
        """
        chist.get_state_with_mask(history, time_idx, cvar, conn_mask, out)

except ImportError:
    LOG.info('Using the python reference implementation for history')

    def get_state(history, time_idx, cvar, node_ids, out):
        """
        Fetches a delayed state from history
        :param history: History array. (time, state_vars, nodes, modes)
        :param time_idx: Delay indices. (nodes, 1 nodes)
        :param cvar: Coupled vars indices. (1, ncvar, 1)
        :param: out: The delayed states (nodes, ncvar, nodes, modes)
        """
        out[...] = history[time_idx, cvar, node_ids, :]



class BaseHistory(StaticAttr):
    "Abstract base class for history implementations."

    n_time, n_node, n_cvar, n_mode = Dim(), Dim(), Dim(), Dim()

    weights = NDArray((n_node, n_node), 'f') # type: numpy.ndarray
    delays = NDArray((n_node, n_node), 'f') # type: numpy.ndarray
    cvars = NDArray((n_cvar, ), 'i') # type: numpy.ndarray

    @property
    def nbytes(self):
        arrays = 'weights delays cvars'.split()
        return sum([getattr(self, ary).nbytes for ary in arrays])

    def __init__(self, weights, delays, cvars, n_mode):
        self.n_time, self.n_cvar, self.n_node, self.n_mode = delays.max() + 1, len(cvars), delays.shape[0], n_mode
        self.weights = weights
        self.delays = delays
        self.cvars = cvars

    def initialize(self, init):
        raise NotImplemented

    def update(self, step, new_state):
        raise NotImplemented

    def query(self, step, out=None):
        raise NotImplemented


class DenseHistory(BaseHistory):
    "TVB's traditional history implementation."

    # extended shape arrays for indexing
    _es = 'n_node', 'n_cvar', 'n_node'
    es_icvar = NDArray(_es, 'i')
    es_idelays = NDArray(_es, 'i')
    es_weights = NDArray(_es + ('n_mode', ), 'f')
    es_node_ids = NDArray(_es, 'i')
    buffer = NDArray(('n_time', 'n_cvar', 'n_node', 'n_mode'), 'f', read_only=False)
    current_state = NDArray(('n_cvar', 'n_node', 'n_mode'), 'f', read_only=False)
    delayed_state = NDArray(('n_node', 'n_cvar', 'n_node', 'n_mode'), 'f', read_only=False)

    @property
    def nbytes(self):
        arrays = 'icvar idelays weights node_ids'.split()
        nbytes = sum([getattr(self, 'es_' + ary).nbytes for ary in arrays])
        nbytes += self.buffer.nbytes
        nbytes += BaseHistory.nbytes.fget(self)
        return nbytes

    def __init__(self, weights, delays, cvars, n_mode):
        super(DenseHistory, self).__init__(weights, delays, cvars, n_mode)

        # initialize indexing arrays
        na = numpy.newaxis
        self.es_icvar = numpy.r_[:len(self.cvars)][na, :, na]
        self.es_idelays = self.delays[:, na, :].astype('i')
        self.es_weights = self.weights[:, na, :, na]
        self.es_node_ids = numpy.r_[:self.n_node][na, na, :]

    def initialize(self, init):
        if init.shape[1] > len(self.cvars):
            init = init[:, self.cvars] # simulator still thinks history is (time, svar, ..)
        self.buffer = init

    def query(self, step, out=None):
        time_idx = (step - 1 - self.es_idelays + self.n_time) % self.n_time
        self.delayed_state = self.buffer[time_idx, self.es_icvar, self.es_node_ids]
        self.current_state = self.buffer[(step - 1) % self.n_time]
        return self.current_state, self.delayed_state

    def update(self, step, new_state):
        self.buffer[step % self.n_time] = new_state[self.cvars]


class SparseNearestHistory(DenseHistory):
    pass

# implement in order  NumPy, Numba & OpenCL versions

# simulator.history becomes impl instance

# state must also transpose for performance reasons

# bench history impl like other components

# trace history accesses

# cfun must also now expect to operate on (nnz, ncvar, nmode)