# -*- coding: utf-8 -*-
"""
This module defines the functions used to compute loss ratio and loss curves
using the probabilistic event based approach.
"""

import math
from numpy import array # pylint: disable=E1101, E0611
from numpy import linspace # pylint: disable=E1101, E0611
from numpy import histogram # pylint: disable=E1101, E0611
from numpy import where # pylint: disable=E1101, E0611

from openquake import shapes

DEFAULT_NUMBER_OF_SAMPLES = 25

def compute_loss_ratios(vuln_function, ground_motion_field):
    """Compute loss ratios using the ground motion field passed."""
    if vuln_function == shapes.EMPTY_CURVE or not \
                        ground_motion_field["IMLs"]:
        return []
    
    imls = vuln_function.abscissae
    loss_ratios = []
    
    # seems like with numpy you can only specify a single fill value
    # if the x_new is outside the range. Here we need two different values,
    # depending if the x_new is below or upon the defined values
    for ground_motion_value in ground_motion_field["IMLs"]:
        if ground_motion_value < imls[0]:
            loss_ratios.append(0.0)
        elif ground_motion_value > imls[-1]:
            loss_ratios.append(imls[-1])
        else:
            loss_ratios.append(vuln_function.ordinate_for(
                    ground_motion_value))
    
    return array(loss_ratios)


def compute_loss_ratios_range(vuln_function):
    """Compute the range of loss ratios used to build the loss ratio curve."""
    loss_ratios = vuln_function.ordinates[:, 0]
    return linspace(0.0, loss_ratios[-1], num=DEFAULT_NUMBER_OF_SAMPLES)


def compute_cumulative_histogram(loss_ratios, loss_ratios_range):
    "Compute the cumulative histogram."
    
    def invalid_ratios(ratios):
        invalids = where(ratios <= 0.0)
        # TODO(JMC): I think this is wrong. where doesn't return zero values
        if invalids:
         return len(invalids[0])
        return None
    
    hist = histogram(loss_ratios, bins=loss_ratios_range)
    hist = hist[0][::-1].cumsum()[::-1]

    # ratios with value 0.0 must be deleted
    hist[0] = hist[0] - invalid_ratios(loss_ratios)
    return hist


def compute_rates_of_exceedance(cum_histogram, tses):
    """Compute the rates of exceedance for the given ground motion
    field and cumulative histogram."""
    if tses <= 0:
        raise ValueError("TSES is not supposed to be less than zero!")
    
    return (array(cum_histogram).astype(float) / tses)


def compute_probs_of_exceedance(rates_of_exceedance, time_span):
    """Compute the probabilities of exceedance for the given ground
    motion field and rates of exceedance."""
    probs_of_exceedance = []
    for idx in xrange(len(rates_of_exceedance)):
        probs_of_exceedance.append(1 - math.exp(
                (rates_of_exceedance[idx] * -1) \
                *  time_span))
    
    return array(probs_of_exceedance)


def compute_loss_ratio_curve(vuln_function, ground_motion_field):
    """Compute the loss ratio curve using the probailistic event approach."""
    loss_ratios = compute_loss_ratios(vuln_function, ground_motion_field)
    loss_ratios_range = compute_loss_ratios_range(vuln_function)
    
    probs_of_exceedance = compute_probs_of_exceedance(
            compute_rates_of_exceedance(compute_cumulative_histogram(
            loss_ratios, loss_ratios_range), ground_motion_field["TSES"]),
            ground_motion_field["TimeSpan"])

    data = []
    for idx in xrange(len(loss_ratios_range) - 1):
        mean_loss_ratios = (loss_ratios_range[idx] + \
                loss_ratios_range[idx + 1]) / 2
        data.append((mean_loss_ratios, probs_of_exceedance[idx]))
    
    return shapes.Curve(data)


# TODO (ac): Test with pre computed data!
def compute_loss_ratio_curve_from_aggregate(aggregate_hist, tses, time_span):
    probs_of_exceedance = compute_probs_of_exceedance(
            compute_rates_of_exceedance(aggregate_hist.compute(),
            tses), time_span)

# TODO (ac): Duplicated from compute_loss_ratio_curve, can be extracted
    data = []
    for idx in xrange(len(aggregate_hist.bins) - 1):
        mean_loss_ratios = (aggregate_hist.bins[idx] + \
                aggregate_hist.bins[idx + 1]) / 2
        data.append((mean_loss_ratios, probs_of_exceedance[idx]))

    return shapes.Curve(data)


def compute_conditional_loss(loss_curve, probability):
    """Returns the loss corresponding to the given probability of exceedance.
    
    This function returns zero if the probability of exceedance if out of bounds.
    The same applies for loss ratio curves.

    """

    probabilities = list(loss_curve.ordinates)
    probabilities.sort(reverse=True)
    
    # the probability we want to use is out of bounds
    if probability > probabilities[0] or probability < probabilities[-1]:
        return 0.0
    
    # find the upper bound
    for index in range(len(probabilities)):
        if probabilities[index] > probability:
            upper_bound = index
    
    lower_bound = upper_bound + 1
    
    # For more information about the math, check the scientific
    # model at <http://to_be_defined> (LRM chapter)
    x = probabilities[lower_bound] - probability
    x *= loss_curve.abscissa_for(probabilities[upper_bound])       
    y = (probability - probabilities[upper_bound]) * \
            loss_curve.abscissa_for(probabilities[lower_bound])
    
    return (x + y) / (probabilities[lower_bound] - probabilities[upper_bound])

class AggregateHistogram(object):
    """This class computes an aggregate histogram."""

    def __init__(self, number_of_bins):
        self.min = 0.0
        self.max = 0.0

        self.distribution = []
        self.number_of_bins = number_of_bins

    @property
    def bins(self):
        return linspace(self.min, self.max, self.number_of_bins)

# TODO (ac): Not tested yet, need pre computed test data!
    def append(self, vuln_function, ground_motion_field):
        """Append this vulnerability function and gmf to the set
        of input data used to compute the aggregate histogram."""
        self._append(compute_loss_ratios(vuln_function, ground_motion_field),
                compute_loss_ratios_range(vuln_function))

    def _append(self, distribution, bins):
        """Append this distribution to the set of distributions used
        to compute the aggregate histogram. Update also (with the given bins)
        the min/max boundaries used to compute the bins for the
        aggregate histogram."""
        self.distribution.extend(distribution)

        if bins[0] < self.min:
            self.min = bins[0]
        
        if bins[-1] > self.max:
            self.max = bins[-1]
    
    def compute(self):
        """Compute the aggregate histogram."""
        return histogram(self.distribution, self.bins)[0]
