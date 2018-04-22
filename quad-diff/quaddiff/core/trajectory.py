"""Trajectory Module."""

import sys
from multiprocessing import Pool
import logging
import cmath as cm
import numpy as np
from tqdm import tqdm
from scipy.integrate import solve_ivp

from ..utils import simplify_trajectory
from .monodromy import Monodromy
from ..utils import MethodProxy
from .constants import *


class Trajectory(object):

    def __init__(self, trajectory, basepoint=None):
        self.trajectory = trajectory
        self.basepoint = basepoint

    def __repr__(self):
        msg = 'Trayectory object based at {}\n'
        msg = msg.format(self.basepoint)
        msg += '\t path : {}'.format(str(self.trajectory))
        return msg

    def simplify(self, distance_2line=DISTANCE_2LINE, min_distance=MIN_DISTANCE):
        simplified = simplify_trajectory(
            self.trajectory,
            distance_2line=distance_2line,
            min_distance=min_distance)
        return Trajectory(simplified, basepoint=self.basepoint)

    def refine(self, max_distance=MAX_DISTANCE):
        refined_trajectory = []
        for point, next_point in zip(self[:-1], self[1:]):
            dist = abs(point - next_point)
            if dist > max_distance:
                times = np.arange(0, dist, max_distance)
                direction = (next_point - point)
                direction /= abs(direction)
                refined_subinterval = point + times * direction
                refined_trajectory += refined_subinterval.tolist()
            else:
                refined_trajectory += [point]
        print(len(refined_trajectory))
        return refined_trajectory

    def __getitem__(self, key):
        return self.trajectory[key]

    def __setitem__(self, key, value):
        self.trajectory[key] = value

    def __iter__(self):
        return iter(self.trajectory)

    def __reversed__(self):
        return reversed(self.trajectory)

    def __contains__(self, item):
        return item in self.trajectory

    def __len__(self):
        return len(self.trajectory)

    def intersects(self, other, simplify=False):
        if simplify:
            line = self.simplify()
        else:
            line = self.trajectory

        line_array = np.array(line)
        other_array = np.array(other)

        # Create meshgrid to compare all segments simultaneously
        # Mesgrids are shifted by one to obtain segments ends.
        Z1, W1 = np.meshgrid(line_array[:-1], other_array[:-1])
        Z2, W2 = np.meshgrid(line_array[1:], other_array[1:])

        # Transform to take one segment to [0, 1] (x -> (x - z1)/ (z2 - z1))
        T1 = ((W2 - Z1) / (Z2 - Z1))
        T2 = ((W1 - Z1) / (Z2 - Z1))

        # First vector crosses the line defined by the second?
        condition1 = T1.imag * T2.imag <= 0

        # Crosses in between?
        zero = np.divide(  # pylint: disable=no-member
            T2.imag * T1.real - T1.imag * T2.real,
            T2.imag - T1.imag,
            where=condition1)
        condition2 = (zero >= 0) & (zero <= 1)

        intersections = condition1 & condition2

        return intersections.any()

    def converges(self, point, distance_2limit=DISTANCE_2LIMIT):
        start = self[0]
        end = self[-1]

        start_close_to_point = abs(point - start) <= distance_2limit
        end_close_to_point = abs(point - end) <= distance_2limit
        return start_close_to_point | end_close_to_point


class TrajectorySolver(object):
    """clase que representa una trayectoria y los metodos para calcularla."""

    max_time = MAX_TIME
    velocity_scale = VELOCITY_SCALE
    lim = LIM
    max_step = MAX_STEP
    close_2pole = CLOSE_2POLE
    close_2start = CLOSE_2START
    close_2zero = CLOSE_2ZERO
    center = 0j

    def __init__(self, quad):
        self.qd = quad  # pylint: disable=invalid-name

    def get_parameters(self):
        parameters = {
            'max_time': self.max_time,
            'max_step': self.max_step,
            'velocity_scale': self.velocity_scale,
            'lim': self.lim,
            'close_2pole': self.close_2pole,
            'close_2start': self.close_2start,
            'close_2zero': self.close_2zero,
            'center': self.center}
        return parameters

    def calculate(self, point, phase=None):
        """Calculate trajectory."""
        if phase is None:
            phase = self.qd.phase

        parameters = self.get_parameters()

        # TODO
        positive_trajectory = calculate_ray_3(
            point, self.qd, parameters=parameters, phase=phase)
        negative_trajectory = calculate_ray_3(
            point, self.qd, sign=-1, parameters=parameters, phase=phase)

        trajectory = list(reversed(negative_trajectory)) + \
            positive_trajectory[1:]
        return Trajectory(trajectory, point)

    def parallel_calculate(self, args, progressbar=True):
        """
        Computes several trajectories in parallel
        solver.parallel_calculate( args)
        where args is a list [(point, phase)]
        """
        pickable_method = MethodProxy(self, self._calculate)

        pool = Pool()
        if progressbar:
            iterable = tqdm(args)
        else:
            iterable = args

        trajectories = pool.imap(pickable_method, iterable)
        pool.close()
        pool.join()

        result = {}
        for arg, trajectory in zip(args, trajectories):
            result[arg] = trajectory
        return result

    def _calculate(self, arg):
        point, phase = arg
        trajectory = self.calculate(point, phase=phase)
        #return (arg, trajectory)
        return trajectory


def calculate_ray(
        starting_point,
        quad,
        sign=1,
        parameters=None,
        phase=None):

    """Calculate a ray solution to the Quadratic Differential."""
    # Check for new phase
    if phase is None:
        phase = quad.phase

    # Check for parameters
    if parameters is None:
        parameters = {}

    velocity_scale = parameters.get('velocity_scale', VELOCITY_SCALE)

    # Monodromy
    m_point = [quad(starting_point, normalize=True)]
    m_phase = [cm.phase(m_point[0])]

    # Get quadratic differential information
    zeros = quad.zeros
    simple_poles = quad.smplpoles
    double_poles = quad.dblpoles
    lim = parameters.get('lim', LIM)
    def vector_field(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)

        # Unbound quadratic differential evaluation
        value = phase
        for zero in zeros:
            value *= ((comp - zero) / abs(comp - zero))

        for smppole in simple_poles:
            value *= ((comp - smppole) / abs(comp - smppole))**-1

        for dblpole in double_poles:
            value *= ((comp - dblpole) / abs(comp - dblpole))**-2

        # Update monodromy
        arg_change = cm.phase(value / m_point[0])
        m_phase[0] += arg_change
        m_point[0] = value

        # Select sqrt branch
        if (m_phase[0] - cm.pi) % (4 * cm.pi) < (2 * cm.pi):
            factor = -1
        else:
            factor = 1

        value = sign * velocity_scale * factor * cm.sqrt(value.conjugate())
        if abs(comp) > lim / 3.0:
            value *= abs(comp)

        return value.real, value.imag

    # Termination events
    far = parameters.get('lim', LIM)
    center = parameters.get('center', 0j)
    def far_away(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        return abs(comp - center) < far
    far_away.terminal = True

    close = parameters.get('close_2pole', CLOSE_2POLE)
    def close_2pole(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        distance = far
        for pole in simple_poles:
            distance = min(distance, abs(comp - pole))
        for pole in double_poles:
            distance = min(distance, abs(comp - pole))
        return distance > close
    close_2pole.terminal = True

    close = parameters.get('close_2start', CLOSE_2START)
    def close_2start(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        return (t < 100) + (abs(comp - starting_point) > close)
    close_2start.terminal = True

    close = parameters.get('close_2zero', CLOSE_2ZERO)
    def close_2zero(t, y):
        comp = complex(*y)
        distance = far
        for zero in zeros:
            distance = min(distance, abs(zero - comp))
        return distance > close
    close_2zero.terminal = True

    # Calculate solution with solve_ivp
    max_time = parameters.get('max_time', MAX_TIME)
    max_step = parameters.get('max_step', MAX_STEP)
    solution = solve_ivp(
        vector_field,
        (0, max_time),
        np.array([starting_point.real, starting_point.imag]),
        events=[far_away, close_2pole, close_2start, close_2zero],
        max_step=max_step)

    return [complex(*point) for point in solution['y'].T]

def calculate_ray_2(
        starting_point,
        quad,
        sign=1,
        parameters=None,
        phase=None):

    # Check for new phase
    if phase is None:
        phase = quad.phase

    # Check for parameters
    if parameters is None:
        parameters = {}

    # Monodromy
    sqrt_monodromy = Monodromy(quad(starting_point))

    velocity_scale = parameters.get('velocity_scale', VELOCITY_SCALE)
    def vector_field(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)

        value = sqrt_monodromy(quad(comp, phase=phase, normalize=True).conjugate())
        value *= velocity_scale
        if abs(comp) > parameters.get('lim', LIM) / 3.0:
            value *= abs(comp)
        value *= sign
        return value.real, value.imag

    # Termination events
    far = parameters.get('lim', LIM)
    center = parameters.get('center', 0j)
    def far_away(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        return 1 - (abs(comp - center) > far)
    far_away.terminal = True

    close = parameters.get('close_2pole', CLOSE_2POLE)
    def close_2pole(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        return quad.distance_2poles(comp) - close
    close_2pole.terminal = True

    close = parameters.get('close_2start', CLOSE_2START)
    def close_2start(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        if t >= 100:
            return abs(comp - starting_point) - close
        else:
            return 1
    close_2start.terminal = True

    close = parameters.get('close_2zero', CLOSE_2ZERO)
    def close_2zero(t, y):
        comp = complex(*y)
        return quad.distance_2zeros(comp) - close
    close_2zero.terminal = True

    # Calculate solution with solve_ivp
    max_time = parameters.get('max_time', MAX_TIME)
    max_step = parameters.get('max_step', MAX_STEP)
    solution = solve_ivp(
        vector_field,
        (0, max_time),
        np.array([starting_point.real, starting_point.imag]),
        events=[far_away, close_2pole, close_2start, close_2zero],
        max_step=max_step)

    return [complex(*point) for point in solution['y'].T]


def calculate_ray_3(
        starting_point,
        quad,
        sign=1,
        parameters=None,
        phase=None):

    """Calculate a ray solution to the Quadratic Differential."""
    # Check for new phase
    if phase is None:
        phase = quad.phase

    # Check for parameters
    if parameters is None:
        parameters = {}

    velocity_scale = parameters.get('velocity_scale', VELOCITY_SCALE)

    # Monodromy
    m_point = [quad(starting_point, normalize=True)]
    m_phase = [cm.phase(m_point[0])]

    # Get quadratic differential information
    zeros = quad.zeros
    simple_poles = quad.smplpoles
    double_poles = quad.dblpoles
    lim = parameters.get('lim', LIM)
    def vector_field(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)

        # Unbound quadratic differential evaluation
        value = reduce(lambda x, z: x * ((comp - z) / abs(comp - z)), zeros, phase)
        value = reduce(lambda x, z: x * ((comp - z) / abs(comp - z))**-1, simple_poles, value)
        value = reduce(lambda x, z: x * ((comp - z) / abs(comp - z))**-2, double_poles, value)

        # Update monodromy
        arg_change = cm.phase(value / m_point[0])
        m_phase[0] += arg_change
        m_point[0] = value

        # Select sqrt branch
        if (m_phase[0] - cm.pi) % (4 * cm.pi) < (2 * cm.pi):
            factor = -1
        else:
            factor = 1

        value = sign * velocity_scale * factor * cm.sqrt(value.conjugate())
        if abs(comp) > lim / 3.0:
            value *= abs(comp)

        return value.real, value.imag

    # Termination events
    far = parameters.get('lim', LIM)
    center = parameters.get('center', 0j)
    def far_away(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        return abs(comp - center) < far
    far_away.terminal = True

    close = parameters.get('close_2pole', CLOSE_2POLE)
    def close_2pole(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        distance = far
        for pole in simple_poles:
            distance = min(distance, abs(comp - pole))
        for pole in double_poles:
            distance = min(distance, abs(comp - pole))
        return distance > close
    close_2pole.terminal = True

    close = parameters.get('close_2start', CLOSE_2START)
    def close_2start(t, y):  # pylint: disable=invalid-name
        comp = complex(*y)
        return (t < 100) + (abs(comp - starting_point) > close)
    close_2start.terminal = True

    close = parameters.get('close_2zero', CLOSE_2ZERO)
    def close_2zero(t, y):
        comp = complex(*y)
        distance = far
        for zero in zeros:
            distance = min(distance, abs(zero - comp))
        return distance > close
    close_2zero.terminal = True

    # Calculate solution with solve_ivp
    max_time = parameters.get('max_time', MAX_TIME)
    max_step = parameters.get('max_step', MAX_STEP)
    solution = solve_ivp(
        vector_field,
        (0, max_time),
        np.array([starting_point.real, starting_point.imag]),
        events=[far_away, close_2pole, close_2start, close_2zero],
        max_step=max_step)

    return [complex(*point) for point in solution['y'].T]


