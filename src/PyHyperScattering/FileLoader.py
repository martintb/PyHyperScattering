import xarray as xr
import pandas as pd
import os
import re
from collections import defaultdict
import warnings
import math
import numpy as np
import pathlib

class FileLoader():
    '''
    Abstract class defining a generic scattering file loader.  Input is a (or multiple) filename/s and output is a
    xarray I(pix_x,pix_y,dims,coords) where dims and coords are loaded by user request.
    
    
     Difference: all coords are dims but not all dims are coords.  Dims can also be auto-hinted using the following   
     standard names: energy,exposure,pos_x,pos_y,pos_z,theta.
     
     Individual loaders can try searching metadata for other dim names but this is not guaranteed.
     Coords can be used to provide a list of values for a dimension when that dimension cannot be hinted, e.g. where vals
     come from external data.
    '''
    file_ext = ''  # file extension to be used to filter files from this instrument
    md_loading_is_quick = False
    
    def loadSingleImage(self,filepath,coords=None,return_q=None):
        raise NotImplementedError
    
    def peekAtMd(self,filepath):
        return self.loadSingleImage(filepath,{})
    

    def loadFileSeries(self,basepath,dims,coords={},file_filter='',file_skip='donotskip',md_filter={},quiet=False,output_qxy=False,dest_qx=None,dest_qy=None):
        '''
        Load a series into a single xarray.
        
        Args:
            basepath (str or Path): path to the directory to load
            dims (list): dimensions of the resulting xarray, as list of str
            coords (dict): dictionary of any dims that are *not* present in metadata
            file_filter (str): string that must be in each file name
            file_skip (str): string that, if present in file name, means file should be skipped.
            md_filter (dict): dict of *required* metadata values; points without these metadata values will be dropped
            quiet (bool): skip printing most intermediate output if true.
            output_qxy (bool): output a qx/qy stack rather than a pix_x/pix_y stack.  This is a lossy operation, the array will be remeshed.  Not recommended.
            dest_qx (array-like or None): set of qx points that you would like the final stack to have.  If None, will take the middle image and remesh to that.
            dest_qy (array-like or None): set of qy points that you would like the final stack to have.  If None, will take the middle image and remesh to that.
        
        '''
        if type(basepath) != pathlib.Path:
            basepath = pathlib.Path(basepath)
        nfiles = len(os.listdir(basepath))
        nprocessed = 0
        filesintegrated = 0
        print(f'Found {str(nfiles)} files.')
        data_rows = []
        qnew = None
        dest_coords = defaultdict(list)
        for file in os.listdir(basepath):
            nprocessed += 1
            local_coords = {}
            if (re.match(self.file_ext,file) is not None) and file_filter in file and file_skip not in file:
                for key,value in coords.items():
                    local_coords[key] = value[file] 
                if self.md_loading_is_quick:
                    #if metadata loading is quick, we can just peek at the metadata and decide what to do
                    md = self.peekAtMd(basepath/file)
                    img = None
                else:
                    img = self.loadSingleImage(basepath/file,coords=local_coords)
                    md = self.peekAtMd(basepath/file)
                load_this_image = True
                for key,val in md_filter.items():
                    if md[key] != md_filter[key]:
                        load_this_image = False
                        if not quiet:
                            print(f'Not loading {file}, expected {key} to be {val} but it was {md[key]}')
                if load_this_image:
                    if img == None:
                        if not quiet:
                            print(f'Loading {file}')
                        img = self.loadSingleImage(basepath/file,coords=local_coords, return_q = output_qxy)
                        # this is a dataarray with dims ['pix_x', 'pix_y']+attrs (standardized)
                        # e.g. generated by img = xr.DataArray(img,dims=['pix_x','pix_y'],
                        #      coords={},attrs=headerdict)
                    is_duplicate = []
                    
                    try:
                        reshaped_md = [{key:dest_coords[key][i] for key in dest_coords.keys()} for i in range(len(list(dest_coords.values())[0]))]
                        # Tyler Martin (tyler.martin@nist.gov) personally claims that the preceding line is the most Pythonic line of code he has
                        # ever seen, and respectfully submits that he should be declared BDFL on the basis of this line.
                        # Seriously, all this does is take dest_coords (a dict of lists) and reshape it to a list of dicts, it's just a mess.
                        
                        for entry in reshaped_md:
                            duplicate = True
                            for key,val in entry.items():
                                if img.attrs[key] != val:
                                    duplicate = False
                            if duplicate:
                                break
                    except IndexError: # handle the edge case of the first run, where dest_coords has no keys.  Can't be a duplicate if there's nothing to duplicate ;)
                        duplicate=False
                    if duplicate:
                        warnings.warn(f'Duplicate image detected while loading... skipping this image {img.attrs}',stacklevel=2)
                    else:
                        data_rows.append(img)
                        for dim in dims:
                            dest_coords[dim].append(img.attrs[dim])

                #update_progress(float(nprocessed)/nfiles,prestring="Loading file " + str(nprocessed) + " of "+
                #    str(nfiles)+" -- "+file)
        #prepare the index...
        dest_coords_sorted = sorted(dest_coords.items())
        
        vals = []
        keys = []
        
        for key,val in dest_coords_sorted:
            vals.append(val)
            keys.append(key)

        #index = pd.MultiIndex.from_arrays(
        #        list(dest_coords.values()
        #        ),
        #    names=
        #    list(dest_coords.keys()
        #    )
        #)
        index = pd.MultiIndex.from_arrays(vals,names=keys)
        index.name = 'system'
        if output_qxy:
            #come up with destination qx/qy here
            if 'energy' in dest_coords.keys():
                en_sorted = np.sort(dest_coords['energy'])
                target_energy = en_sorted[math.floor(len(en_sorted)/2)]
                #target_energy=np.median(dest_coords['energy']) should work but doesn't bc of edge case w even num pts
                for n,e in enumerate(dest_coords['energy']):
                    if e == target_energy:
                        dest_row = n
                        break
            else:
                dest_row = math.floor(len(data_rows)/2)
            if dest_qx is None: dest_qx = data_rows[dest_row].qx
            if dest_qy is None: dest_qy = data_rows[dest_row].qy
            data_rows_transformed = []
            for row in data_rows:
                data_rows_transformed.append(
                    row.interp(coords={'qx':dest_qx,'qy':dest_qy}))
            data_rows = data_rows_transformed
        out = xr.concat(data_rows,dim=index)
        out.attrs.update({'dims_unpacked':dims})
        if not output_qxy:
            out = out.assign_coords({'pix_x':np.arange(0,len(out.pix_x)),'pix_y':np.arange(0,len(out.pix_y))})
        return out