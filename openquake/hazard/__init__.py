"""
Ostensibly core computation methods for hazard engine. Most of the hard
computation is backed by the Java HazardEngine via the hazard_wrapper
"""

# hazard tokens
SOURCE_MODEL_TOKEN = 'sources'
GMPE_TOKEN = 'gmpe'
JOB_TOKEN = 'job'
ERF_KEY_TOKEN = 'erf'
MGM_KEY_TOKEN = 'mgm'
HAZARD_CURVE_KEY_TOKEN = 'hazard_curve'
STOCHASTIC_SET_TOKEN = 'ses'
