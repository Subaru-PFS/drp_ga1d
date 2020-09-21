# drp_ga1d
A data analysis pipeline for the Galactic Archaeology science

September 21, 2020

Now includes basic elements of PFS GA 1D abundance pipeline, adapted from the spectral synthesis method of
Escala et al. 2019. Measures T_eff, [Fe/H], and [alpha/Fe] from spectroscopy, including an optional flag (fit_logg=True) to measure surface gravity as a free parameter (currently in development).

* pfs_io.py

Generates a PFS object dictionary initialized with inputs and outputs of the abundance pipeline.

Inputs: pfsObject*, pfsConfig* FITS files containing spectral information, targetting information, and user-specified resolution mode and site-specific object identifier.

Outputs: pfsAbund* FITS file containing information from PFS object dictionary.

* pfs_1d_abund.py

Inputs: PFSObject dictionary with necessary information to perform abundance measurement
Outputs: Modified PFSObject dictionary with updated keywords corresponding to the output
         of the PFS 1d abundance pipeline.
         
* pfs_utilities.py, pfs_phot.py, and pfs_read_synth.py provide helper functions for pfs_1d_abund.py
         
NOTE: In order to use this pipeline, you MUST have access to a continuum-normalized grids
of synthethic spectra over the appropriate wavelength range, such as the grids of
Escala et al. 2019 and Kirby et al. 2008.

You also must use the wavelength masks constructed for each arm of PFS contained in the specregion folders
(generated by I. Escala, 2018).

Dependencies: 
ASTROPY (http://www.astropy.org/)

Usage: 
See Jupyter iPython notebook for an example.

