from PIL import Image
import os
import pathlib
import xarray as xr
import pandas as pd
import warnings
import json
from pyFAI import azimuthalIntegrator
import numpy as np

class SST1RSoXSLoader():
    #Loader for TIFF files form NSLS-II SST1 RSoXS instrument

    def __init__(self,corr_mode=None,user_corr_fun=None,dark_pedestal=0,exposure_offset=0):
        #Params:
        #
        # corr_mode = origin to use for the intensity correction.  Can be 'expt','i0','expt+i0','user_func','old',or 'none'
        # user_corr_func = a callable that takes the header dictionary and returns the value of the correction.
        # dark_pedestal = value to add to the whole image before doing dark subtraction, to avoid non-negative values.
        # exposure_offset = value to add to the exposure time.  Measured at 2ms with the piezo shutter in Dec 2019 by Jacob Thelen, NIST
        #

        if corr_mode == None:
            warnings.warn("Correction mode was not set, not performing *any* intensity corrections.  Are you sure this is "+
                          "right? Set corr_mode to 'none' to suppress this warning.")
            self.corr_mode = 'none'
        else:
            self.corr_mode = corr_mode
        # self.dark_pedestal = dark_pedestal
        # self.user_corr_func = user_corr_func
        # self.exposure_offset = exposure_offset
        # self.darks = {}
    # def loadFileSeries(self,basepath):
    #     try:
    #         flist = list(basepath.glob('*primary*.tiff'))
    #     except AttributeError:
    #         basepath = pathlib.Path(basepath)
    #         flist = list(basepath.glob('*primary*.tiff'))
    #     print(f'Found {str(len(flist))} files.')
    #
    #     out = xr.DataArray()
    #     for file in flist:
    #         single_img = self.loadSingleImage(file)
    #         out = xr.concat(out,single_img)
    #
    #     return out
    def integrate_image(self, xr_img,npts=500,integration_method='csr_ocl'):
        self.integrator = azimuthalIntegrator.AzimuthalIntegrator(dist=xr_img.sdd/1000,
                                                poni1=xr_img.beamcenter_y*.06/1000,
                                                poni2=xr_img.beamcenter_x*.06/1000,
                                                pixel1=.06/1000,
                                                pixel2=.06/1000,
                                                wavelength=xr_img.wavelength)
        TwoD = self.integrator.integrate2d(xr_img.values,npts,
                                correctSolidAngle=True,
                                error_model='azimuthal',
                                unit='q_A^-1',
                                method=integration_method)

        return xr.DataArray(TwoD.intensity,dims=['chi','q'],coords={'q':TwoD.radial,'chi':TwoD.azimuthal},attrs=xr_img.attrs)


    def loadSingleImage(self,filepath,coords=None):
        img = Image.open(filepath)

        headerdict = self.loadMd(filepath)
        # two steps in this pre-processing stage:
        #     (1) get and apply the right scalar correction term to the image
        #     (2) find and subtract the right dark
        if coords != None:
            headerdict.update(coords)

        #step 1: correction term

        if self.corr_mode == 'expt':
            corr = headerdict['exposure'] #(headerdict['AI 3 Izero']*expt)
        elif self.corr_mode == 'i0':
            corr = headerdict['AI 3 Izero']
        elif self.corr_mode == 'expt+i0':
            corr = headerdict['exposure'] * headerdict['AI 3 Izero']
        elif self.corr_mode == 'user_func':
            corr = self.user_corr_func(headerdict)
        elif self.corr_mode == 'old':
            corr = headerdict['AI 6 BeamStop'] * 2.4e10/ headerdict['Beamline Energy'] / headerdict['AI 3 Izero']
            #this term is a mess...  @TODO check where it comes from
        else:
            corr = 1

        if(corr<0):
            warnings.warn(f'Correction value is negative: {corr} with headers {headerdict}.')
            corr = abs(corr)


        # # step 2: dark subtraction
        # try:
        #     darkimg = self.darks[headerdict['EXPOSURE']]
        # except KeyError:
        #     warnings.warn(f"Could not find a dark image with exposure time {headerdict['EXPOSURE']}.  Using zeros.")
        #     darkimg = np.zeros_like(img)

        # img = (img-darkimg+self.dark_pedestal)/corr
        qpx = 2*np.pi*60e-6/(headerdict['sdd']/1000)/(headerdict['wavelength']*1e10)
        qx = (np.arange(1,img.size[0]+1)-headerdict['beamcenter_x'])*qpx
        qy = (np.arange(1,img.size[1]+1)-headerdict['beamcenter_y'])*qpx
        # now, match up the dims and coords
        return xr.DataArray(img,dims=['qy','qx'],coords={'qy':qy,'qx':qx},attrs=headerdict)


    def read_json(self,jsonfile):
        json_dict = {}
        with open(jsonfile) as f:
            data = json.load(f)
        if data[1]['RSoXS_Config'] == 'SAXS':
            json_dict['rsoxs_config'] = 'saxs'
            # discrepency between what is in .json and actual
            json_dict['beamcenter_x'] = 367#data[1]['RSoXS_SAXS_BCX']
            json_dict['beamcenter_y'] = 479 #data[1]['RSoXS_SAXS_BCY']
            json_dict['sdd'] = data[1]['RSoXS_SAXS_SDD']

        elif data[1]['RSoXS_Config'] == 'WAXS':
            json_dict['rsoxs_config'] = 'waxs'
            json_dict['beamcenter_x'] = 399 #data[1]['RSoXS_WAXS_BCX']
            json_dict['beamcenter_y'] = 526 #data[1]['RSoXS_WAXS_BCY']
            json_dict['sdd'] = data[1]['RSoXS_WAXS_SDD']

        else:
            json_dict['rsoxs_config'] == 'unknown'
            warnings.warn('RSoXS_Config is neither SAXS or WAXS. Check json file')

        return json_dict

    def read_baseline(self,baseline_csv):
        baseline_dict = {}
        df_baseline = pd.read_csv(baseline_csv)
        baseline_dict['sam_x'] = round(df_baseline['RSoXS Sample Outboard-Inboard'][0],4)
        baseline_dict['sam_y'] = round(df_baseline['RSoXS Sample Up-Down'][0],4)
        baseline_dict['sam_z'] = round(df_baseline['RSoXS Sample Downstream-Upstream'][0],4)
        baseline_dict['sam_th'] = round(df_baseline['RSoXS Sample Rotation'][0],4)

        return baseline_dict

    def read_primary(self,primary_csv,json_dict,seq_num):
        primary_dict = {}
        df_primary = pd.read_csv(primary_csv)
        if json_dict['rsoxs_config'] == 'waxs':
            primary_dict['exposure'] = df_primary['Wide Angle CCD Detector_cam_acquire_time'][seq_num]
        elif json_dict['rsoxs_config'] == 'saxs':
            primary_dict['exposure'] = df_primary['Small Angle CCD Detector_cam_acquire_time'][seq_num]
        else:
            warnings.warn('Check rsoxs_config in json file')

        primary_dict['energy'] = round(df_primary['en_energy_setpoint'][seq_num],4)
        primary_dict['polarization'] = df_primary['en_polarization_setpoint'][seq_num]

        return primary_dict


    def loadMd(self,filepath):
        # get sequence number of image for primary csv
        fname = os.path.basename(filepath)
        split_fname = fname.split('-')
        seq_num = int(split_fname[-1][:-5])
        scan_id = split_fname[0]

        # This allows for passing just the filename without the full path
        dirPath = os.path.dirname(filepath)

        if dirPath == '':
            cwd = pathlib.Path('.').absolute()

            json_fname = list(cwd.glob('*.jsonl'))
            json_dict = self.read_json(json_fname[0])

            baseline_fname = list(cwd.glob('*baseline.csv'))
            baseline_dict = self.read_baseline(baseline_fname[0])


            primary_path = os.path.dirname(cwd)
            primary_fname = list(primary_path.glob(f'{scan_id}*primary.csv'))
            primary_dict = self.read_primary(primary_fname[0],json_dict,seq_num)
        else:
            json_fname = list(pathlib.Path(dirPath).glob('*jsonl'))
            json_dict = self.read_json(json_fname[0])

            baseline_fname = list(pathlib.Path(dirPath).glob('*baseline.csv'))
            baseline_dict = self.read_baseline(baseline_fname[0])

            primary_path = os.path.dirname(dirPath)
            primary_fname = list(pathlib.Path(primary_path).glob(f'{scan_id}*primary.csv'))
            primary_dict = self.read_primary(primary_fname[0],json_dict,seq_num)

        headerdict = {**primary_dict,**baseline_dict,**json_dict}
        headerdict['wavelength'] = 1.239842e-6 / headerdict['energy']
        headerdict['seq_num'] = seq_num
        headerdict['sampleid'] = scan_id
        return headerdict