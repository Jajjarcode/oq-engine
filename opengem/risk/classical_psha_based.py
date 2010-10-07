# -*- coding: utf-8 -*-
"""
This module defines the computations used in the
probabilistic scenario. For more information, take a look at the
scientific model at <http://to_be_defined>.

Here is a brief explanation on how to use these functions to
compute a loss ratio or loss curve.

Input parameters: a vulnerability curve, an hazard curve and
the asset value (if you want to compute a loss curve)

NOTE(fab): These functions have been changed. They don't expect the 
vulnerability curve code to be passed in, but the vulnerability curve 
object (of type FastCurve) itself.

Output values: a loss ratio or loss curve

Step 1: the loss ratios defined in the vulnerabilility function
need to be splitted

Currently a vulnerability function is defined as a set of
pairs where the first value is the intensity measure level and
the second value is a tuple containing the loss ratio mean as
first element and the loss ratio cov as second element. It
seems like domain experts prefer to have (conceptually) a single
function with both mean and cov, while I used in the GEM1 risk
engine a single function for the mean and a single function
for the cov.

loss_ratios = [value[0] for value in vuln_function.codomain] # get the means
loss_ratios.insert(0, 0.0) # we need to add 0.0 as first value
loss_ratios = _split_loss_ratios(loss_ratios)

These loss ratios can be cached because they depend on the
vulnerability function and the vulnerability function depends
on the asset, so we will have lots of sites with same values.

Step 2: build the loss ratio exceedance matrix

lrem = _compute_lrem(loss_ratios, vuln_function)

The same as above regarding caching (caching the lrem really
improves the performance!).

Step 3: build the loss ratio * probability of occurrence matrix

lrem_po = compute_lrem_po(vuln_function, lrem, hazard_curve)

We could think to cache also this stuff. It's true that an hazard curve
is probably different per each site, but in the risk engine we generally use a
resolution that is higher than in the hazard engine.

Step 4: build the loss ratio curve

loss_ratio_curve = compute_loss_ratio_curve(loss_ratios, lrem_po)

Step 5: build the loss curve

loss_curve = compute_loss_curve(loss_ratio_curve, asset)
"""

import numpy
import scipy # pylint: disable=F0401

from numpy import isnan # pylint: disable=F0401,E0611
from scipy import sqrt # pylint: disable=F0401,E0611
from scipy import stats # pylint: disable=F0401,E0611
from scipy import log # pylint: disable=F0401,E0611

from opengem import identifiers
from opengem import logs
from opengem import memcached
from opengem import shapes

logger = logs.RISK_LOG

STEPS_PER_INTERVAL = 5

def compute_loss_ratio_curve(vuln_function, hazard_curve):
    """One of the two main public functions, this produces
    a loss ratio curve for a specific hazard curve (e.g., site),
    by applying a given vulnerability function."""
    
    if vuln_function is None:
        vuln_function = shapes.EMPTY_CURVE

    lrem = _compute_lrem(vuln_function)
    lrem_po = _compute_lrem_po(vuln_function, lrem, hazard_curve)
    loss_ratios = _generate_loss_ratios(vuln_function)
    loss_ratio_curve = _compute_loss_ratio_curve_from_lrem_po(
                            loss_ratios, lrem_po)
    return loss_ratio_curve


def compute_loss_curve(loss_ratio_curve, asset):
    """Computes the loss curve for a specific asset value."""

    if not asset: 
        return shapes.EMPTY_CURVE # invalid asset

    loss_curve_values = []
    for loss_ratio, probability_occurrence \
            in loss_ratio_curve.values.iteritems():
        logger.debug("Loss ratio is %s, PO is %s" % (
            loss_ratio, probability_occurrence))
        key = "%s" % (float(loss_ratio) * asset)
        loss_curve_values.append((key, probability_occurrence))

    return shapes.FastCurve(loss_curve_values)

def _compute_lrem_po(vuln_function, lrem, hazard_curve):
    """Computes the loss ratio * probability of occurrence matrix.""" 
    
    current_column = 0
    lrem_po = [None] * len(lrem)

    # we need to process intensity measure levels in ascending order
    imls = list(vuln_function.domain)
    imls.sort()
    
    for iml in imls:
        prob_occ = hazard_curve.get_for(iml)
        for row in range(len(lrem_po)):
            if not lrem_po[row]: 
                lrem_po[row] = [None] * len(vuln_function.domain)
            lrem_po[row][current_column] = lrem[row][current_column] * prob_occ
        current_column += 1
    
    return lrem_po


def _compute_loss_ratio_curve_from_lrem_po(loss_ratios, lrem_po):
    """Computes the loss ratio curve."""
    
    loss_ratio_curve_values = []
    for row in range(len(lrem_po)-1):
        prob_occ = 0.0
        for column in range(len(lrem_po[row])):
            prob_occ += lrem_po[row][column]
        loss_ratio_curve_values.append(("%s" % loss_ratios[row], prob_occ))

    return shapes.FastCurve(loss_ratio_curve_values)

#@state.memoize
def _generate_loss_ratios(vuln_function):
    """Loss ratios are a function of the vulnerability curve"""

    loss_ratios = [value[0] for value in vuln_function.codomain] 
        # get the means
    loss_ratios.insert(0, 0.0)
        # we need to add 0.0 as first value
    splitted = _split_loss_ratios(loss_ratios)  
    return splitted

# @state.memoize
def _compute_lrem(vuln_function, distribution=None):
    """Computes the loss ratio exceedance matrix."""
    
    if not distribution:
        distribution = stats.lognorm
        # This is so we can memoize the thing

    loss_ratios = _generate_loss_ratios(vuln_function)
    
    current_column = 0
    lrem = [None] * (len(loss_ratios)+1)

    # we need to process intensity measure levels in ascending order
    imls = list(vuln_function.domain)
    imls.sort()

    def fix_value(prob):
        """Fix negative probabilities for values close to zero. 
        Same behaviour in Java, so it probably depends on how 
        floating point values are handled internally"""
        if isnan(prob):
            return 0.0
        if prob < 0.00001: 
            return 0.0
        else: 
            return prob

    for iml in imls:
        # we need to use std deviation, but we have cov
        cov = float(vuln_function.get_for(iml)[1])
        mean = float(vuln_function.get_for(iml)[0])
        stddev = cov * mean
        variance = stddev ** 2.0
        mu = log(mean ** 2.0 / sqrt(variance + mean ** 2.0) )
        sigma = sqrt(log((variance / mean ** 2.0) + 1.0))
        
        for row in range(len(loss_ratios)+1):
            if not lrem[row]: 
                lrem[row] = [None] * len(vuln_function.domain)
            # last loss ratio is fixed to be 1
            if row < len(loss_ratios): 
                next_ratio = loss_ratios[row]
            else: 
                next_ratio = 1.0 
            lrem[row][current_column] = fix_value(
                distribution.sf(next_ratio, 
                        sigma, scale=scipy.exp(mu)))
        current_column += 1
    return lrem


def _split_loss_ratios(loss_ratios, steps_per_interval=STEPS_PER_INTERVAL):
    """Splits the loss ratios.
    
    steps_per_interval is the number of steps we make to go from one loss
    ratio to the other. For example, if we have [1.0, 2.0]:

        steps_per_interval = 1 produces [1.0, 2.0]
        steps_per_interval = 2 produces [1.0, 1.5, 2.0]
        steps_per_interval = 3 produces [1.0, 1.33, 1.66, 2.0]
    """

    splitted_loss_ratios = []
    
    for i in range(len(loss_ratios) - 1):
        if not i: # lower bound added only in the first interval
            splitted_loss_ratios.append(loss_ratios[i])
        
        offset = (loss_ratios[i + 1] - loss_ratios[i]) / steps_per_interval
        
        for j in range(steps_per_interval - 1):
            splitted_loss_ratios.append(loss_ratios[i] + (offset * (j + 1)))
    
        splitted_loss_ratios.append(loss_ratios[i + 1])
        
    return splitted_loss_ratios


def compute_conditional_loss(loss_curve, probability):
    """Returns the loss corresponding to the given probability of exceedance.
    
    This function returns zero if the probability of exceedance if out of bounds.
    The same applies for loss ratio curves.
    """

    probabilities = loss_curve.codomain
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
    x *= loss_curve.domain_for(probabilities[upper_bound])       
    y = (probability - probabilities[upper_bound]) * \
            loss_curve.domain_for(probabilities[lower_bound])
    
    return (x + y) / (probabilities[lower_bound] - probabilities[upper_bound])

def compute_mid_mean_pe(loss_ratio_pe_curve):
    # compute mean PE values 
    # This function needs to take the first two values of the LR and compute 
    # the mean, then iterate to the next two, and so on
    loss_ratio_pe_mid_curve = []
            
    for i in xrange(len(loss_ratio_pe_curve.codomain)-1):

        current_value = loss_ratio_pe_curve.codomain[i]
        next_value = loss_ratio_pe_curve.codomain[i + 1]
        
        loss_ratio_pe_mid_curve.append(
            numpy.mean([current_value, next_value]))
          
    return loss_ratio_pe_mid_curve
          
def compute_mid_po(loss_ratio_pe_mid_curve):
    # compute the PO values
    #
    loss_ratio_po_mid_curve = []
    
    for j in xrange(len(loss_ratio_pe_mid_curve)-1):
        current_value = loss_ratio_pe_mid_curve[j]
        next_value = loss_ratio_pe_mid_curve[j + 1]
        
        loss_ratio_po_mid_curve.append(current_value - next_value)

    return loss_ratio_po_mid_curve
        
def compute_mean_loss(loss_ratio_pe_curve, loss_ratio_po_mid_curve):
    # compute sum of every PO and LR
    #
    mean_loss_ratio = []
  
    loss_ratio_pe_curve_float = map(float, loss_ratio_pe_curve.domain)

    mean_loss_ratio = sum(i*j for i, j in zip(loss_ratio_pe_curve_float, 
        loss_ratio_po_mid_curve))

    logger.debug('%s= mean_loss_ratio', mean_loss_ratio)