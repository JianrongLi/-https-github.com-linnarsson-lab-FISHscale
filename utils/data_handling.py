from os import path, makedirs
from glob import glob
from dask import dataframe as dd
from typing import Optional, Dict, Any
import numpy as np
import itertools
import pandas as pd
import pickle
try:
    from pyarrow.parquet import ParquetFile
except ModuleNotFoundError as e:
    print(f'Please install "pyarrow" to load ".parquet" files. Without only .csv files are supported which are memory inefficient. Error: {e}')

class DataLoader():
           
    def _check_parsed(self, filename: str, reparse: bool) -> bool:
        """Check if data has already been parsed

        Args:
            filename (str): path to data file.
            reparse (bool): True if data needs to be reparsed

        Returns:
            bool: True if folder with name <dataset_name>_FISHscale_Data is 
                present and contains at least one ".parquet" file.
        """
        if path.exists(self.FISHscale_data_folder):
            fn = path.join(self.FISHscale_data_folder, '*.parquet')
            file_list = glob(fn)
            if len(file_list) > 0:
                if not reparse:
                    self.vp(f'Found {len(file_list)} already parsed files. Skipping parsing.')
                return True
            else:
                return False          

        else:
            return False
        
    def _metadatafile_make(self, data_dict: Dict):
        """Make a metadata file. This is a pickled dictionary.

        Args:
            data_dict (Dict): Dictionary with metadata

        Raises:
            Exception: If input is not a dictionary.
        """
        if not isinstance(data_dict, dict):
            raise Exception(f'Input should be a dictionary, not {type(data_dict)}.')
        
        file_name = path.join(self.FISHscale_data_folder, f'{self.dataset_name}_metadata.pkl')
        with open(file_name, 'wb') as pf:
            pickle.dump(data_dict, pf)
        
    def _metadatafile_read(self) -> Dict:
        """Read the full metadata file and return the dictionary.

        Raises:
            Exception: If metadata file was not found.
            Exception: If metadata file could not be opened.
            Exception: If the metadata is not a dictionary.

        Returns:
            Dict: Metadata dictionary
        """
        file_name = path.join(self.FISHscale_data_folder, f'{self.dataset_name}_metadata.pkl')
        
        try:
            with open(file_name, 'rb') as pf:
                prop = pickle.load(pf)
        except FileNotFoundError as e:
            print('Metadata file was not found, please reparse.')
            raise e
        except Exception as e:
            print('Could not open metadata file please reparse or remake.')
            raise e
        
        if not isinstance(prop, dict):
            raise Exception(f'Metadata file should be a dictionary, not {type(prop)}.')
        
        return prop
        
    def _metadatafile_add(self, data_dict: Dict):
        """Add data to the metadata file.
        
        If key already exists it will be overwritten.

        Args:
            data_dict (Dict): Data to add.

        Raises:
            Exception: If "data_dict" is not a dictionary.
        """
        if not isinstance(data_dict, dict):
            raise Exception(f'Input should be a dictionary, not {type(data_dict)}.')
        
        existing_dict = self._metadatafile_read()
        merged_dict = {**existing_dict, **data_dict}
        
        self._metadatafile_make(merged_dict)
        
    def _metadatafile_get(self, item: str, verbose=False) -> Any:
        """Get a single item from the metadata.
        
        Retruns False when key is not present.

        Args:
            item (str): Key of the item to return
            verbose (bool, optional): If True, print the error messages if a
                key can not be found. Defaults to False.

        Returns:
            Any: The item if the key was present, or False if it was not.
        """
        
        existing_dict = self._metadatafile_read()
        try:
            result = existing_dict[item]
            return result
        except KeyError:
            if verbose:
                print(f'Key {item} not present in metadata.')
                print(f'Existing keys: {existing_dict.keys()}')
            return False

    def _coordinate_properties(self, data):
        """Calculate properties of coordinates.

        Calculates XY min, max, extend and center of the points. 
        """
        self.x_min, self.x_max, self.y_min, self.y_max = data.x.min(), data.x.max(), data.y.min(), data.y.max()
        self.x_extend = self.x_max - self.x_min
        self.y_extend = self.y_max - self.y_min 
        self.xy_center = (self.x_max - 0.5*self.x_extend, self.y_max - 0.5*self.y_extend)
        
        prop = {'x_min': self.x_min,
                'x_max': self.x_max,
                'y_min': self.y_min,
                'y_max': self.y_max}
        self._metadatafile_make(prop)

    def _get_coordinate_properties(self):
        """Get the properties of coordinates.
        """
        #Load metadata
        prop = self._metadatafile_read()
        #Set properties
        self.x_min = prop['x_min']
        self.x_max = prop['x_max']
        self.y_min = prop['y_min']
        self.y_max = prop['y_max']
        self.x_extend = self.x_max - self.x_min
        self.y_extend = self.y_max - self.y_min 
        self.xy_center = (self.x_max - 0.5*self.x_extend, self.y_max - 0.5*self.y_extend)

    def _gb_dump(self, data, name, folder_name:str):
        """Save groupby results as .parquet files.

        Args:
            x ([type]): groupby result.
            name ([type]): Dataset name.
            folder_name (str): Folder path.
            z (float): Z coordinate.
        """
        fn_out = path.join(folder_name, f'{name}_{data.name}.parquet')
        #write data
        data.to_parquet(fn_out)

    def load_data(self, filename: str, x_label: str, y_label: str, gene_label: str, other_columns: Optional[list], 
                  x_offset: float, y_offset: float, z_offset: float, pixel_size: str, unique_genes: Optional[np.ndarray],
                  reparse: bool = False) -> Any:             
        """Load data from data file.
        
        Opens a file containing XY coordinates of points with a gene label.
        The data will be parsed by grouping the data by the gene label and 
        storing the results in individual .parquet files for each gene. These
        files then get read as a single Dask Dataframe, to be memory efficient.
        The parsed data will be saved in a folder next to the original data,
        and once parsed, reopening the file will skip the parsing. If you want
        to explicity reparse, use the "reparse" option.
        
        Note: Parsing will save the offsets. To change the offset the data
            needs to be reparsed unfortunately. 
        Note: The original data file needs to fit into ram RAM.

        Args:
            filename (str): Path to the datafile to load. Should be in .parquet
                or .csv format.
            x_label (str, optional): Name of the column of the Pandas dataframe
                that contains the X coordinates of the points. Defaults to 
                'r_px_microscope_stitched'.
            y_label (str, optional): Name of the column of the Pandas dataframe
                that contains the Y coordinates of the points. Defaults to 
                'c_px_microscope_stitched'.
            gene_label (str, optional):  Name of the column of the Pandas 
                dataframe that contains the gene labels of the points. 
                Defaults to 'below3Hdistance_genes'.
            other_columns (list, optional): List with labels of other columns 
                that need to be loaded. Data will stored under "self.other"
                as Pandas Dataframe. Defaults to None.
            x_offset (float, optional): Offset in X axis.
            y_offset (float, optional): Offset in Y axis.
            z_offset (float, optional): Offset in Z axis.
            pixel_size (str, optional): Size of the pixels in micrometer.
            reparse (bool, optional): True if you want to reparse the data,
                if False, it will repeat the parsing. Parsing will apply the
                offset. Defaults to False.

        Raises:
            IOError: If file can not be opened.

        Returns:
            Dask Dataframe: With all data and partitioned by gene.
        """
        new_parse = False
        #Check if data has already been parsed
        if not self._check_parsed(filename, reparse) or reparse:
            new_parse = True
            
            #Data parsing
            if filename.endswith('.parquet') or filename.endswith('.csv'):
                
                # .parquet files
                if filename.endswith('.parquet'):
                    #Dask Dataframe
                    #open_f = lambda f, c: dd.read_parquet(f, columns = c)
                    #Pandas Dataframe, This turned out to be faster and more RAM effcient.
                    open_f = lambda f, c: pd.read_parquet(f, columns = c)
                # .csv files
                else:
                    open_f = lambda f, c: dd.read_csv(f, usecols = c)
                
                #Get columns to open
                col_to_open = [[gene_label, x_label, y_label], other_columns]
                col_to_open = list(itertools.chain.from_iterable(col_to_open))
                rename_col = dict(zip([gene_label, x_label, y_label], ['g', 'x', 'y']))
                
                #Read the data file
                data = open_f(filename, col_to_open)
                data = data.rename(columns = rename_col)
                
                #Get data shape
                self.shape = data.shape
                
                #Offset data
                if x_offset !=0 and y_offset != 0:
                    data.loc[:, ['x', 'y']] += [x_offset, y_offset]
                    
                #Add z_offset
                self.z += z_offset
                data['z'] = self.z
                
                #Scale the data
                if pixel_size != 1:
                    data.loc[:, ['x', 'y']] = data.loc[:, ['x', 'y']] * pixel_size
                
                #Find data extend and make metadata file
                self._coordinate_properties(data)

                #unique genes
                if not isinstance(unique_genes, np.ndarray):
                    self.unique_genes = np.unique(data.g)
                else:
                    self.unique_genes = unique_genes
                self._metadatafile_add({'unique_genes': self.unique_genes})

                #Group the data by gene and save
                data.groupby('g').apply(lambda x: self._gb_dump(x, self.dataset_name, self.FISHscale_data_folder))#, meta=('float64')).compute()
                
            else:
                raise IOError (f'Invalid file type: {filename}, should be in ".parquet" or ".csv" format.') 
                   
        #Load Dask Dataframe from the parsed gene dataframes
        makedirs(self.FISHscale_data_folder, exist_ok=True)
        self.df = dd.read_parquet(path.join(self.FISHscale_data_folder, '*.parquet'))
        
        if new_parse == False:
            #Get coordinate properties from metadata
            self._get_coordinate_properties()
            #Unique genes
            unique_genes_metadata = self._metadatafile_get('unique_genes')
            #Check if unique_genes are given by user
            if isinstance(unique_genes, np.ndarray):
                self.unique_genes = unique_genes
                self._metadatafile_add({'unique_genes': self.unique_genes})
            #Check if unique genes could be found in metadata
            elif isinstance(unique_genes_metadata, np.ndarray): 
                self.unique_genes = unique_genes_metadata
            #Calcualte the unique genes, slow
            else:
                self.unique_genes = self.df.g.drop_duplicates().compute().to_numpy()
                self._metadatafile_add({'unique_genes': self.unique_genes})
    
    def _get_gene_n_points(self) -> Dict:
        """Get number of points per gene.
        
        Uses the metadata of the .parquet files for each gene.

        Returns:
            List: Number of points per gene in the order of 
                "self.unique_genes".
        """       
        gene_n_points = {}
        for g in self.unique_genes:
            f = glob(path.join(self.FISHscale_data_folder, f'*{g}.parquet'))
            pfile = ParquetFile(f[0])
            gene_n_points[g] = pfile.metadata.num_rows
            
        return gene_n_points
    
    def transpose(self):
        """Transpose data. Switches X and Y.
        
        This does NOT survive realoading the data
        """
        rename_col = {'x': 'y', 'y': 'x'}
        self.df = self.df.rename(columns = rename_col)
        self.x_min, self.y_min, self.x_max, self.y_max = self.y_min, self.x_min, self.y_max, self.x_max
        self.x_extend = self.x_max - self.x_min
        self.y_extend = self.y_max - self.y_min 
        self.xy_center = (self.x_max - 0.5*self.x_extend, self.y_max - 0.5*self.y_extend)
    
    def get_dask_attrs_rows(self,l:list):
        """
        Get rows by index

        Args:
            l (list): list of indexes to get from self.df

        Returns:
            dask.dataframe: filtered dask dataframe
        """        
        return self.df.map_partitions(lambda x: x[x.index.isin(l)])

    def add_dask_attribute(self,name:str,l:list):
        """
        [summary]

        Args:
            name (str): column name
            l (list): list of features
        """        
        self.dask_attrs = self.dask_attrs.merge(pd.DataFrame({name:l}))
        
        
        