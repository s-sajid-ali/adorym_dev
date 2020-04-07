import numpy as np
import os
import h5py
from mpi4py import MPI
import gc

from adorym.util import *
import adorym.wrappers as w

comm = MPI.COMM_WORLD
n_ranks = comm.Get_size()
rank = comm.Get_rank()

class LargeArray(object):

    def __init__(self, full_size, shared_file_object=False, monochannel=False, output_folder=None):
        self.full_size = full_size
        self.shared_file_object=shared_file_object
        self.monochannel = monochannel
        self.output_folder = output_folder
        self.f = None
        self.dset = None
        self.arr_0 = None

    def create_file_object(self, fname, use_checkpoint=False):
        fmode = 'a' if use_checkpoint else 'w'
        try:
            self.f = h5py.File(os.path.join(self.output_folder, fname), fmode, driver='mpio', comm=comm)
        except:
            self.f = h5py.File(os.path.join(self.output_folder, fname), fmode)
        try:
            # If dataset doesn't exist, create it.
            self.dset = self.f.create_dataset('obj', shape=self.full_size, dtype='float64')
        except:
            # If dataset exists, create a pointer to it.
            self.dset = self.f['obj']

    def read_chunks_from_file(self, this_pos_batch, probe_size, dset_2=None, device=None, unknown_type='delta_beta'):
        dset = self.dset if dset_2 is None else dset_2
        obj = get_rotated_subblocks(dset, this_pos_batch, probe_size,
                                    self.full_size, monochannel=self.monochannel, unknown_type=unknown_type)
        self.arr_0 = np.copy(obj)
        obj = w.create_variable(obj, device=device)
        return obj

    def rotate_data_in_file(self, coords, interpolation='bilinear', dset_2=None, precalculate_rotation_coords=True):
        apply_rotation_to_hdf5(self.dset, coords, rank, n_ranks, interpolation=interpolation,
                               monochannel=self.monochannel, dset_2=dset_2, precalculate_rotation_coords=precalculate_rotation_coords)

    def reverse_rotate_data_in_file(self, coords, interpolation='bilinear', precalculate_rotation_coords=True):
        revert_rotation_to_hdf5(self.dset, coords, rank, n_ranks, interpolation=interpolation,
                                monochannel=self.monochannel, precalculate_rotation_coords=precalculate_rotation_coords)

    def write_chunks_to_file(self, this_pos_batch, arr_channel_0, arr_channel_1, probe_size, write_difference=True, dset_2=None):
        dset = self.dset if dset_2 is None else dset_2
        arr_channel_0 = w.to_numpy(arr_channel_0)
        if arr_channel_1 is not None: arr_channel_1 = w.to_numpy(arr_channel_1)
        if write_difference:
            if self.monochannel:
                arr_channel_0 = arr_channel_0 - self.arr_0
                arr_channel_0 /= n_ranks
            else:
                arr_channel_0 = arr_channel_0 - np.take(self.arr_0, 0, axis=-1)
                arr_channel_1 = arr_channel_1 - np.take(self.arr_0, 1, axis=-1)
                arr_channel_0 /= n_ranks
                arr_channel_1 /= n_ranks
        write_subblocks_to_file(dset, this_pos_batch, arr_channel_0, arr_channel_1,
                                probe_size, self.full_size, monochannel=self.monochannel)


class ObjectFunction(LargeArray):

    def __init__(self, full_size, shared_file_object=False, output_folder=None, ds_level=1,
                 object_type='normal'):
        super(ObjectFunction, self).__init__(full_size, shared_file_object=shared_file_object,
                                             monochannel=False, output_folder=output_folder)
        self.delta = None
        self.beta = None
        self.ds_level = ds_level
        self.object_type = object_type
        self.f_rot = None
        self.dset_rot = None

    def create_file_object(self, use_checkpoint=False):
        super(ObjectFunction, self).create_file_object('intermediate_obj.h5', use_checkpoint=use_checkpoint)

    def create_temporary_file_object(self):
        """
        This file is used to save rotated object.
        """
        try:
            self.f_rot = h5py.File(os.path.join(self.output_folder, 'intermediate_obj_rot.h5'), 'w', driver='mpio', comm=comm)
        except:
            self.f_rot = h5py.File(os.path.join(self.output_folder, 'intermediate_obj_rot.h5'), 'w')
        self.dset_rot = self.f_rot.create_dataset('obj', shape=self.full_size, dtype='float64')

    def initialize_array(self, save_stdout=None, timestr=None, not_first_level=False, initial_guess=None, device=None,
                         random_guess_means_sigmas=(8.7e-7, 5.1e-8, 1e-7, 1e-8), unknown_type='delta_beta'):
        temp_delta, temp_beta = \
            initialize_object(self.full_size[:-1], dset=None, ds_level=self.ds_level, object_type=self.object_type,
                              initial_guess=initial_guess, output_folder=self.output_folder, rank=rank,
                              n_ranks=n_ranks, save_stdout=save_stdout, timestr=timestr,
                              shared_file_object=False, not_first_level=not_first_level,
                              random_guess_means_sigmas=random_guess_means_sigmas, unknown_type=unknown_type)
        self.delta = w.create_variable(temp_delta, device=device, requires_grad=True)
        self.beta = w.create_variable(temp_beta, device=device, requires_grad=True)
        del temp_delta
        del temp_beta
        gc.collect()

    def initialize_array_with_values(self, obj_delta, obj_beta, device=None):
        self.delta = w.create_variable(obj_delta, device=device, requires_grad=True)
        self.beta = w.create_variable(obj_beta, device=device, requires_grad=True)

    def initialize_file_object(self, save_stdout=None, timestr=None, not_first_level=False, initial_guess=None,
                               random_guess_means_sigmas=(8.7e-7, 5.1e-8, 1e-7, 1e-8), unknown_type='delta_beta'):
        initialize_object(self.full_size[:-1], dset=self.dset, ds_level=self.ds_level, object_type=self.object_type,
                          initial_guess=initial_guess, output_folder=self.output_folder, rank=rank,
                          n_ranks=n_ranks, save_stdout=save_stdout, timestr=timestr,
                          shared_file_object=True, not_first_level=not_first_level, random_guess_means_sigmas=random_guess_means_sigmas,
                          unknown_type=unknown_type)

    def apply_finite_support_mask_to_array(self, mask, unknown_type='delta_beta', device=None):
        assert isinstance(mask, Mask)
        if not self.shared_file_object:
            with w.no_grad():
                if unknown_type == 'delta_beta':
                    self.delta *= mask.mask
                    self.beta *= mask.mask
                elif unknown_type == 'real_imag':
                    ones_arr = w.ones_like(self.delta, requires_grad=False, device=device)
                    zeros_arr = w.zeros_like(self.delta, requires_grad=False, device=device)
                    self.delta = self.delta * mask.mask + ones_arr * (1 - mask.mask)
                    self.beta = self.beta * mask.mask + zeros_arr * (1 - mask.mask)
            w.reattach(self.delta)
            w.reattach(self.beta)

    def apply_finite_support_mask_to_file(self, mask, unknown_type='delta_beta', device=None):
        assert isinstance(mask, Mask)
        if self.shared_file_object:
            slice_ls = range(rank, self.full_size[0], n_ranks)
            if unknown_type == 'real_imag':
                ones_arr = w.ones(mask.dset.shape[1:3], requires_grad=False, device=device)
                zeros_arr = w.zeros(mask.dset.shape[1:3], requires_grad=False, device=device)
            for i_slice in slice_ls:
                mask_arr = mask.dset[i_slice]
                obj_arr = self.dset[i_slice]
                if unknown_type == 'delta_beta':
                    obj_arr[:, :, 0] *= mask_arr
                    obj_arr[:, :, 1] *= mask_arr
                elif unknown_type == 'real_imag':
                    obj_arr[:, :, 0] = obj_arr[:, :, 0] * mask_arr + ones_arr * (1 - mask_arr)
                    obj_arr[:, :, 1] = obj_arr[:, :, 1] * mask_arr + zeros_arr * (1 - mask_arr)
                self.dset[i_slice] = obj_arr

    def update_object(self, obj_delta, obj_beta):
        self.delta.detach()
        self.beta.detach()
        self.delta = obj_delta
        self.beta = obj_beta


class Gradient(ObjectFunction):

    def __init__(self, obj):
        assert isinstance(obj, ObjectFunction)
        super(Gradient, self).__init__(obj.full_size, obj.shared_file_object,
                                 obj.output_folder, obj.dset, obj.object_type)

    def create_file_object(self):
        super(ObjectFunction, self).create_file_object('intermediate_grad.h5', use_checkpoint=False)

    def initialize_gradient_file(self):
        initialize_hdf5_with_constant(self.dset, rank, n_ranks)


class Mask(LargeArray):

    def __init__(self, full_size, finite_support_mask_path, shared_file_object=False, output_folder=None, ds_level=1):
        super(Mask, self).__init__(full_size, shared_file_object=shared_file_object,
                                   monochannel=True, output_folder=output_folder)
        self.mask = None
        self.ds_level = ds_level
        self.finite_support_mask_path = finite_support_mask_path

    def create_file_object(self, use_checkpoint=False):
        super(Mask, self).create_file_object('intermediate_mask.h5', use_checkpoint=use_checkpoint)

    def initialize_array_with_values(self, mask, device=None):
        self.mask = w.create_variable(mask, requires_grad=False, device=device)

    def initialize_file_object(self):
        # arr is a memmap.
        arr = dxchange.read_tiff(self.finite_support_mask_path)
        initialize_hdf5_with_arrays(self.dset, rank, n_ranks, arr, None)

    def update_mask_array(self, obj, threshold=1e-9):
        assert isinstance(obj, ObjectFunction)
        obj_arr = obj.delta
        self.mask[obj_arr < threshold] = 0

    def update_mask_file(self, obj, threshold=1e-9):
        assert isinstance(obj, ObjectFunction)
        if self.shared_file_object:
            slice_ls = range(rank, self.full_size[0], n_ranks)
            for i_slice in slice_ls:
                obj_arr = obj.dset[i_slice, :, :, 0]
                mask_arr = self.dset[i_slice, :, :]
                mask_arr[obj_arr < threshold] = 0
                self.dset[i_slice, :, :] = mask_arr