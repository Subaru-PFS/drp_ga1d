"""
@author: I. Escala (Caltech, Princeton)

Parts of PFS 1D Abundance Pipeline, adapted from the spectral synthesis method of
Escala et al. 2019. 

Generates a PFS object dictionary initialized with inputs and outputs of the abundance
pipeline.

Inputs: PFSObject dictionary with necessary information to perform abundance measurement
Outputs: Modified PFSObject dictionary with updated keywords corresponding to the output
         of the PFS 1d abundance pipeline.
         
NOTE: In order to use this pipeline, you MUST have access to a continuum-normalized grids
of synthethic spectra over the appropriate wavelength range, such as the grids of
Escala et al. 2019 and Kirby et al. 2008.

You also must use the wavelength masks constructed for each arm of PFS 
(generated by I. Escala, 2018).

Usage:
-------

Below is an example of proper usage of the MeasurePFSAbund class. This requires usage of
the PFSObject class.

from pfsabund import pfs_io as io #import the source code containing the relevant classes
from pfsabund import pfs_1d_abund as abund

# Specify the tract, patch, catId, objId, and visits according to the PFS data model
# for the 1D combined spectra (PFSObject*.fits) to read in the FITS file and generate a
# PFS object dictionary

pfs = io.Read.read_fits(catId, tract, patch, objId, visit)

## Note that additional keywords to specifiy various local paths are available
abund.MeasurePFSAbund(pfs, mode='mr')

# Write the PFS object dictionary to a FITS file
pfs.write_to()
"""

from __future__ import absolute_import
from scipy.optimize import curve_fit
from pfsabund import pfs_read_synth as read
from pfsabund import pfs_utilities as ut
from pfsabund import pfs_phot as phot
import numpy as np

### Constants
dlam_to_gauss = 1./2.35
spec_res = {'blue': 2.1, 'redlr': 2.7, 'redmr': 1.6, 'nir': 2.4}
spec_coverage = {'bluemr': [3800., 6500.], 'redlr': [6300., 9700.], 'redmr': [7100., 8850.], 
                 'nir': [9400., 12600.], 'bluelr': [3800., 6300.]}
flex_factor = 400.  

#################################################################
#################################################################

class MeasurePFSAbund():

    """
    Abundance measurement class to measure stellar parameters (T_eff) and abundances
    ([Fe/H], [alpha/Fe]) of PFS spectra. log_g is fixed to the photometric value OR
    can be varied as a free parameter using fit_logg=True
    """
    
    def __init__(self, pfs=None, mode=None, root='./', synth_path_blue='../gridie/',
                 synth_path_red='../grid7/', dm=22., ddm=0.1, fit_logg=False):
    
        """
        Create and initialize the attributes of the MeasurePFSAbund class
        """
        
        self.mode = mode #spectral resolution mode - LR or MR
        self.root = root #root directory
        self.dm = dm; self.ddm = ddm #distance modulus parameters
        self.synth_path_blue = synth_path_blue #directory pointing to blue grid
        self.synth_path_red = synth_path_red #directory pointing to red grid
        
        #Calculate photometric quantities to take as input for the abundance pipeline
        #Note for the case of fit_logg=True where the distance modulus is not well
        #known, the photometric estimate returned will not necessarily be valid and
        #will not subsequently be used
        
        ut.io.get_phot(pfs, dm=self.dm, ddm=self.ddm)
        
        #Construct the spectral resolution as a function of wavelength, based on the different
        #modes of PFS 
                
        dlam = [ spec_res['blue'] if (pix > spec_coverage['blue'+self.mode][0]) and\
                (pix < spec_coverage['blue'+self.mode][1]) else spec_res['red'+self.mode]\
                for pix in pfs.prop('wvl') ]
        
        #convert from FWHM to Gaussian width for use with abundance measurement pipeline
        dlam = np.array(dlam)*dlam_to_gauss 
        self.dlam = dlam
        
        #Initialize a hash table to use to store the synthetic spectral data in memory
        self.hash_blue = {}; self.hash_red = {}
        
        self.feh_def = -2. #starting point for metallicity -- this value doesn't matter
        self.alphafe_def = 0.
        self.logg_def = 1.

        #Define convergence criteria for the continuum refinement
        self.maxiter = 50
        self.feh_thresh = 0.001; self.alphafe_thresh = 0.001
        self.teff_thresh = 1.; self.logg_thresh = 0.001
        
        #Store initial guess values in new variables
        self.feh0 = self.feh_def
        self.alphafe0 = self.alphafe_def
        self.teff0 = pfs.prop('teffphot')
        
        #If fitting for the surface gravity, set logg to the default value
        #otherwise, fix to the photometric value
        if fit_logg: self.logg0 = self.logg_def
        else: self.logg = pfs.prop('loggphot')
        
        #Execute abundance measurement
        self.measure_abund(pfs, fit_logg=fit_logg)
        
    def measure_abund(self, pfs=None, fit_logg=False):
    
        """
        Measure T_eff, [Fe/H], and [alpha/Fe] for the observed spectrum
        """
        
        ### Normally a telluric correction / heliocentric correction / shifting the
        ### observed spectrum into the rest frame would be placed here, but currently
        ### not necessary for mock PFS data
        
        #Construct the spectral masks to measure [Fe/H] and [alpha/Fe] abundances
        #from wavelength regions sensitive to a given elemental abundance
        feh_fit_mask, alphafe_fit_mask, spec_mask = self.fit_masks(pfs)
        self.feh_fit_mask = feh_fit_mask
        self.alphafe_fit_mask = alphafe_fit_mask
        
        #Identify whether there is wavelength information in the blue and/or red
        #synthetic spectra ranges
        wb = np.where( (pfs.prop('wvl')[spec_mask] >= 4100.) &\
                       (pfs.prop('wvl')[spec_mask] < 6300.) )[0]
        wr = np.where( (pfs.prop('wvl')[spec_mask] >= 6300.) &\
                       (pfs.prop('wvl')[spec_mask] < 9100.) )[0]
                       
        self.wb = wb
        self.wr = wr
        
        #Perform an initial continuum normalization, using KeckII/DEIMOS 600ZD style 
        #normalization of Escala et al. 2019a as an approximation
        ut.io.continuum_normalize(pfs)
        
        i = 0
        pfs.assign(pfs.prop('initcont'), 'refinedcont')
        
        while i < self.maxiter:
        
            ## Perform the fit for [Fe/H], Dlam, Teff
                
            #Define initial parameters and bounds for fitting process
            
            if fit_logg: # if logg is a free parameter
                params0 = [self.teff0, self.feh0, self.logg0]
                bounds0 = ([3500., -4.5, 0.], [8000., 0., 5.])
            else: 
                params0 = [self.teff0, self.feh0] 
                bounds0 = ([3500., -4.5], [8000., 0.])
            
            params1 = [self.alphafe_def]
            
            #Insert the effective temperature pixel at the beginning of the observed spectrum
            #and the inverse variance array
            flux_teff = np.insert(pfs.prop('flux')[self.feh_fit_mask] /\
                                  pfs.prop('refinedcont')[self.feh_fit_mask],
                                  0, pfs.prop('teffphot'))
                                  
            wvl_teff = np.insert(pfs.prop('wvl')[self.feh_fit_mask], 0, 
                                 pfs.prop('wvl')[self.feh_fit_mask][0])

            sigma_teff0 = ( pfs.prop('ivar')[self.feh_fit_mask] *\
                            pfs.prop('refinedcont')[self.feh_fit_mask]**2. )**(-0.5)
                            
            npix_fit = float(len(sigma_teff0))
            sigma_teff = np.insert(sigma_teff0, 0, pfs.prop('teffphoterr') *\
                                   np.sqrt(flex_factor/npix_fit))

            #If surface gravity is a free parameter
            if fit_logg:
            
                self.best_params0, self.covar0 = curve_fit(lambda x, t, f, g: self.get_synth_step1(x, t, f, logg_fit=g),
                                                           wvl_teff, flux_teff, p0=params0, 
                                                           sigma=sigma_teff, bounds=bounds0, 
                                                           absolute_sigma=True, ftol=1.e-10, 
                                                           gtol=1.e-10, xtol=1.e-10)
                                                           
                self.teff, self.feh, self.logg = self.best_params0
                
            else:
            
                self.best_params0, self.covar0 = curve_fit(lambda x, t, f: self.get_synth_step1(x, t, f, logg_fit=None),
                                                           wvl_teff, flux_teff, p0=params0, 
                                                           sigma=sigma_teff, bounds=bounds0, 
                                                           absolute_sigma=True, ftol=1.e-10, 
                                                           gtol=1.e-10, xtol=1.e-10)
                                                           
                self.teff, self.feh = self.best_params0
                
                                                     
            #Perform the fit [alpha/Fe]
            asigma = (pfs.prop('ivar')[self.alphafe_fit_mask] *\
                      pfs.prop('refinedcont')[self.alphafe_fit_mask]**2. )**(-0.5) 
                      
            aflux = pfs.prop('flux')[self.alphafe_fit_mask] /\
                    pfs.prop('refinedcont')[self.alphafe_fit_mask]
    
            self.best_params1, self.covar1 = curve_fit(self.get_synth_step2, 
                                                       pfs.prop('wvl')[self.alphafe_fit_mask], 
                                                       aflux, p0=params1, sigma=asigma, 
                                                       bounds=([-0.8], [1.2]), 
                                                       absolute_sigma=True, ftol=1.e-10, 
                                                       gtol=1.e-10, xtol=1.e-10)
                                                       
            self.alphafe = self.best_params1[0]

            #Construct the best-fit synthetic spectrum from the best fit parameters
            best_synth = self.get_best_synth(wvl_obs=pfs.prop('wvl'))
            
            #Refine the continuum based on these parameters
            ut.io.continuum_refinement(pfs, best_synth)
            
            #Check if the continuum iteration has converged
            
            final = np.array([self.teff, self.feh, self.alphafe])
            initial = np.array([self.teff0, self.feh0, self.alphafe0])
            thresh = np.array([self.teff_thresh, self.feh_thresh, self.alphafe_thresh])
            
            if fit_logg:
                final = np.append(final, self.logg)
                initial = np.append(initial, self.logg0)
                thresh = np.append(thresh, self.logg_thresh)
                
            converged = np.abs(final - initial) < thresh
            if converged.all(): 
                break
                
            else:
            
                print(final)
                
                self.teff0 = self.teff
                self.feh0 = self.feh
                self.alphafe0 = self.alphafe
                
                if fit_logg:
                    self.logg0 = self.logg
                
                i += 1
                
        if i == self.maxiter:
            print('WARNING: Maximum number of continuum iterations exceeded: exiting '+
                   'continuum refinement loop')
            pfs.assign('converge_flag', 0)
            
        else:
            pfs.assign('converge_flag', 1)
          
        ## Re-determine the metallicity ###
        
        params2 = [self.feh_def]
        
        fflux = pfs.prop('flux')[self.feh_fit_mask] / pfs.prop('refinedcont')[self.feh_fit_mask]
        fsigma = ( pfs.prop('ivar')[self.feh_fit_mask] * pfs.prop('refinedcont')[self.feh_fit_mask]**2. )**(-0.5)
        
        self.best_params2, self.covar = curve_fit(self.get_synth_step3, 
                                        pfs.prop('wvl')[self.feh_fit_mask], fflux,
                                        p0=params2, sigma=fsigma, bounds = ([-4.5], [0.]),
                                        absolute_sigma=True, ftol=1.e-10, gtol=1.e-10,
                                        xtol=1.e-10)
                                        
        #### Now re-determine the total alpha abundance of the atmosphere ####
        
        params3 = [self.alphafe_def]
        
        aflux = pfs.prop('flux')[self.alphafe_fit_mask] / pfs.prop('refinedcont')[self.alphafe_fit_mask]
        asigma = (pfs.prop('ivar')[self.alphafe_fit_mask] * pfs.prop('refinedcont')[self.alphafe_fit_mask]**2.)**(-0.5)
    
        self.best_params3, self.covar3 = curve_fit(self.get_synth_step4, pfs.prop('wvl')[self.alphafe_fit_mask],
                                                   aflux, p0=params3, sigma=asigma,
                                                   bounds=([-0.8],[1.2]), absolute_sigma=True, 
                                                   ftol=1.e-10, gtol=1.e-10, xtol=1.e-10)
                                                   
        #### Recalculate the metallicity a final time ####
        
        params4 = [self.feh_def]

        self.best_params4, self.covar4 = curve_fit(self.get_synth_step5, pfs.prop('wvl')[self.feh_fit_mask], 
                                                    fflux, p0=params4, sigma=fsigma, 
                                                    bounds=([-4.5],[0.]), absolute_sigma=True, 
                                                    ftol=1.e-10, gtol=1.e-10, xtol=1.e-10)
                                                    
        pfs.assign(self.teff, 'teff')
        pfs.assign(self.logg, 'logg')
        pfs.assign(self.best_params4[0], 'feh')
        pfs.assign(self.best_params3[0], 'alphafe')
                                                    
        pfs.assign(np.sqrt(np.diag(self.covar0)[0]), 'tefferr')
        pfs.assign(np.sqrt(np.diag(self.covar4)[0]), 'feherr')
        pfs.assign(np.sqrt(np.diag(self.covar3)[0]), 'alphafeerr')
        
        if fit_logg:
            pfs.assign(np.sqrt(np.diag(self.covar0)[-1]), 'loggerr')
        
        best_synth_final = self.get_best_synth(wvl_obs=pfs.prop('wvl'), teff=pfs.prop('teff'), 
                                               feh=pfs.prop('feh'), logg=pfs.prop('logg'),
                                               alphafe=pfs.prop('alphafe'))
                                               
        pfs.assign(best_synth_final, 'synth')
                                
        print(pfs.prop('teff'), pfs.prop('logg'), pfs.prop('feh'), pfs.prop('alphafe'))
        print(pfs.prop('tefferr'), pfs.prop('loggerr'), pfs.prop('feherr'), pfs.prop('alphafeerr'))
        
        return
    
    def get_synth_step5(self, wvl_fit=None, feh_fit=None):
    
        #Read in the synthetic spectrum
        wvl, synth = self.construct_synth(wvl_fit, self.teff, feh_fit, self.logg,
                                          self.best_params3[0])

        #Smooth the synthetic spectrum and interpolate it onto the observed wavelength array
        if isinstance(self.dlam, list) or isinstance(self.dlam, np.ndarray): 
            synthi = ut.io.smooth_gauss_wrapper(wvl, synth, wvl_fit, self.dlam[self.feh_fit_mask])

        return synthi
    
    def get_synth_step4(self, wvl_fit=None, alphafe_fit=None):
        """
        In this iteration, the effective temperature is held constant at the value from step1, 
        the metallicity is held constant at the previously determined value, and the [alpha/Fe] 
        is re-determined based on the revised observed spectrum
        """
        #Read in the synthetic spectrum
        wvl, synth = self.construct_synth(wvl_fit, self.teff, self.best_params2[0], self.logg, 
                                          alphafe_fit)

        #Smooth the synthetic spectrum and interpolate it onto the observed wavelength array
        if isinstance(self.dlam, list) or isinstance(self.dlam, np.ndarray): 
            synthi = ut.io.smooth_gauss_wrapper(wvl, synth, wvl_fit, self.dlam[self.alphafe_fit_mask])

        return synthi

    def get_synth_step3(self, wvl_fit=None, feh_fit=None):
        """
        In this iteration, the effective temperature is held constant at the values from step1, 
        the alpha abundance is assumed to be solar, and the metallicity is re-determined 
        based on the revised observed spectrum
        """
        
        #Read in the synthetic spectrum
        wvl, synth = self.construct_synth(wvl_fit, self.teff, feh_fit, self.logg, self.alphafe)

        #Smooth the synthetic spectrum and interpolate it onto the observed wavelength array
        if isinstance(self.dlam, list) or isinstance(self.dlam, np.ndarray): 
            synthi = ut.io.smooth_gauss_wrapper(wvl, synth, wvl_fit, self.dlam[self.feh_fit_mask])

        return synthi
                                                     
    def get_synth_step1(self, wvl_fit=None, teff_fit=None, feh_fit=None, logg_fit=None):

        """
        Define the function to be used in the curve fitting, such that 
        ydata = f(xdata, *params) + eps and r = ydata - f(xdata, *params),
        where f is the model function

        In this iteration, the effective temperature is determined within the constraints 
        of photometry and the metallicity is varied. [alpha/Fe] is fixed constant at solar

        Parameters
        ----------
        wvl: array-like: the flattened wavelength array of the observed spectrum
        teff_fit: float: spectroscopic effective temperature, to be fitted
        logg_fit: float, surface gravity to be fitted, if not None
        feh_fit: float: spectroscopic metallicity, to be fitted

        Returns
        -------
        synthi: array-like: the synthethic spectrum with the given
                              spectroscopic effective temperature, metallicity,
                              default alpha abundance, and photometric surface gravity,
                              interpolated onto the same wavelengtha array as the observed
                              spectrum and smoothed to the given FWHM spectral resolution
        """
        
        #Read in the synthetic spectrum with both blue and red components
        
        if logg_fit is not None: #if logg is a free parameter
            wvl, synth = self.construct_synth(wvl_fit, teff_fit, feh_fit, logg_fit, self.alphafe0)
        else:
            wvl, synth = self.construct_synth(wvl_fit, teff_fit, feh_fit, self.logg, self.alphafe0)

        #Smooth the synthetic spectrum and interpolate it onto the observed wavelength array
        #Assume that the spectral resolution is constant for a given arm of PFS
        
        if isinstance(self.dlam, list) or isinstance(self.dlam, np.ndarray): 
            synthi = ut.io.smooth_gauss_wrapper(wvl, synth, wvl_fit[1:], 
                self.dlam[self.feh_fit_mask])
    
        #Insert the effective temperature pixel to the beginning of the synthetic spectrum
        synthi = np.insert(synthi, 0, teff_fit)

        return synthi
        
    def get_synth_step2(self, wvl_fit=None, alphafe_fit=None):
     
            """
            In this iteration, the effective temperature, metallicity, and smoothing parameter
            are held constant at the values from step1, and the alpha abundance is varied.
            """
    
            #Read in the synthetic spectrum
            wvl, synth = self.construct_synth(wvl_fit, self.teff, self.feh, self.logg, 
                         alphafe_fit)

            #Smooth the synthetic spectrum according to the spectral resolution
            if isinstance(self.dlam, list) or isinstance(self.dlam, np.ndarray):
                synthi = ut.io.smooth_gauss_wrapper(wvl, synth, wvl_fit, 
                    self.dlam[self.alphafe_fit_mask])

            return synthi
            
    def get_best_synth(self, wvl_obs=None, teff=None, feh=None, logg=None, alphafe=None):
                       
            if teff is None:
                teff = self.teff
            if feh is None:
                feh = self.feh
            if logg is None:
                logg = self.logg
            if alphafe is None:
                alphafe = self.best_params1[0]
    
            #Read in the synthetic spectrum
            wvl, synth = self.construct_synth(wvl_obs, teff, feh, logg, alphafe)

            #Smooth the synthetic spectrum according to the spectral resolution
            synthi = ut.io.smooth_gauss_wrapper(wvl, synth, wvl_obs, self.dlam)

            return synthi
        
    def construct_synth(self, wvl=None, teff_in=None, feh_in=None, logg_in=None, alphafe_in=None):
        
        """
        Construct a synthetic spectrum to compare to observational data. Helper
        function to the get_synth() functions
        """
        
        #If observational data exists in the wavelength range of the blue grid
        if len(self.wb) > 0:
        
            #Read in the synthetic spectrum from the blue grid
            wvlb, synthb = read.io.read_interp_synth(teff=teff_in,
                logg=logg_in, feh=feh_in, alphafe=alphafe_in, 
                data_path=self.synth_path_blue, hash=self.hash_blue)
            
            #Make sure that the synthetic spectrum is within the data range for observations,
            wsynthb = np.where( (wvlb > wvl.min() ) & (wvlb < wvl.max() ) )[0]
        
        #If observational data exists in the wavelength range of the red grid
        if len(self.wr) > 0:
            
            #Read in the synthetic spectrum from the red grid
            wvlr, synthr = read.io.read_interp_synth(teff=teff_in,
                logg=logg_in, feh=feh_in, alphafe=alphafe_in, 
                data_path=self.synth_path_red, start=6300., sstop=9100., 
                hash=self.hash_red)
            
            wsynthr = np.where( (wvlr > wvl.min() ) & (wvlr < wvl.max() ) )[0]
        
        #Some checks based on where data is present
        if len(self.wb) > 0 and len(self.wr) == 0: 
            wvl_synth = wvlb[wsynthb]
            synth = synthb[wsynthb]
            
        elif len(self.wb) == 0 and len(self.wr) > 0:
            wvl_synth = wvlr[wsynthr]
            synth = relflux_synth_red[wsynthr]
            
        else:
            wvl_synth = np.append(wvlb[wsynthb], wvlr[wsynthr])
            synth = np.append(synthb[wsynthb], synthr[wsynthr])
            
        return wvl_synth, synth
        
    def fit_masks(self, pfs=None):
    
        """
        Read in the data for the PFS masks on the red and blue arms, then construct the
        spectral mask for each elemental abundance
        """
        
        #Check if the mask files exist, and if so, load them
        feh_mask = ut.io.check_mask_file_exists('mask_fe_pfs_final_py3', mode=self.mode,
            root=self.root)
        
        alphafe_mask = ut.io.check_mask_file_exists('mask_alphafe_pfs_final_py3',
            mode=self.mode, root=self.root)
    
        #Construct the mask based on the information in the mask files
        feh_fit_mask = ut.io.construct_mask(pfs, feh_mask)  
        alphafe_fit_mask = ut.io.construct_mask(pfs, alphafe_mask)
        
        #Construct a general mask for the observed spectrum
        spec_mask = ut.io.construct_mask(pfs)
        
        return feh_fit_mask, alphafe_fit_mask, spec_mask
    
    