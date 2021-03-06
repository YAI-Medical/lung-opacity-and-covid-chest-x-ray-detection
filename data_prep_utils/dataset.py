import numpy as np
import pandas as pd
import pydicom
import os
import typing
import random

import torch
from torch.utils.data import IterableDataset, DataLoader
from PIL import Image
from torchvision.datasets import VisionDataset, ImageFolder as _ImageFolder


# loader

def pil_loader(path: str) -> np.ndarray:
    try:
        # open path as file to avoid ResourceWarning (https://github.com/python-pillow/Pillow/issues/835)
        with open(path, 'rb') as f:
            img = Image.open(f)
            # issue: to use one-channel image, we use "L" conversion.
            # L = R * 299/1000 + G * 587/1000 + B * 114/1000
            arr = np.array(img.convert('L'))
            if arr.ndim == 2:
                arr = np.stack([arr]).transpose((1, 2, 0))
            return arr
    except Exception as exc:
        raise OSError("Cannot load image file: {}".format(path)) from exc


def dicom_loader(path: str) -> np.ndarray:
    try:
        with open(path, 'rb') as f:
            dcm = pydicom.dcmread(f)
            arr = dcm.pixel_array
            if arr.ndim == 2:
                arr = np.stack([arr]).transpose((1, 2, 0))
            return arr
    except Exception as exc:
        raise OSError("Cannot load dicom file: {}".format(path)) from exc


def default_loader(path: str):
    if os.path.splitext(path)[-1].lower() == '.dcm':
        return dicom_loader(path)
    else:
        return pil_loader(path)


# dataset class

class ImageWithPandas(VisionDataset):
    """A generic data loader where the image path and label is given as pandas DataFrame.

    Args:
        dataframe (pandas.DataFrame): A data table that contains image path, target class,
            and extra outputs.
        label_id (string): Data frame`s image path label string.
        label_target (string): Data frame`s target class label string.
        root (string, optional): Root directory path. Use unless data frame`s column
            contains file folders.
        extension (string, optional): An extension that will be concatenated after
            image file name. Use unless data frame`s column contains extension.
        class_to_idx (dict[str, int], optional): A mapping table that converts class
            label string into integer value. If not given, sorted index value will
            be used as class integer value.
        transform (callable, optional): A function/transform that takes in an image
            and returns a transformed version. E.g, ``transforms.RandomCrop``
        target_transform (callable, optional): A function/transform that takes in the
            target and transforms it.
        extras_transform (callable, optional): A function/transform that takes in the
            extra outputs and transforms it.
        loader (callable, optional): A function to load an image given its path.

     Attributes:
        classes (list): List of the class names sorted alphabetically.
        class_to_idx (dict): Dict with items (class_name, class_index).
        samples (list): List of (sample path, class_index) tuples
    """

    def __init__(
            self,
            dataframe: pd.DataFrame,
            label_id: str,
            label_target: str,
            root: typing.Optional[typing.Union[str, os.PathLike]] = None,
            extension: typing.Optional[str] = None,
            class_to_idx: typing.Optional[typing.Dict[typing.Any, int]] = None,
            transform: typing.Optional[typing.Callable] = None,
            target_transform: typing.Optional[typing.Callable] = None,
            extras_transform: typing.Optional[typing.Callable] = None,
            loader: typing.Callable[[str], typing.Any] = default_loader,
    ) -> None:

        super(ImageWithPandas, self).__init__(root, None, transform, target_transform)

        self.extras_transform = extras_transform
        self.loader = loader
        self.label_id = label_id
        self.label_target = label_target

        labels = [label_id, label_target]

        samples = dataframe[labels].copy(deep=True)

        assert extension.startswith('.') or extension is None
        if root is not None:
            root = os.path.expanduser(root)
        if root is not None or extension is not None:
            samples[label_id] = samples[label_id].map(
                (lambda x: os.path.join(root, x + extension or ''))
                if root is not None else (lambda x: x + extension)
            )

        classes = sorted(samples[label_target].unique())
        if class_to_idx is None:
            class_to_idx = {cls_name: i for i, cls_name in enumerate(classes)}
        samples[label_target] = samples[label_target].map(lambda x: class_to_idx[x])

        samples = samples.drop_duplicates()
        samples.index = range(len(samples))

        self.samples = samples
        self.classes = classes
        self.class_to_idx = class_to_idx
        self.num_classes = len(class_to_idx)

    def get_labels(self):
        return list(self.samples[self.label_target])

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index: int) -> ...:
        row = self.samples.iloc[index]
        path, target = row[self.label_id], row[self.label_target]
        sample = self.loader(path)
        if self.transform is not None:
            sample = self.transform(sample)
        if self.target_transform is not None:
            target = self.target_transform(target)
        else:
            target = np.array(target)
        return sample, target


class ImageBboxWithPandas(VisionDataset):
    """A generic data loader where the image path and label is given as pandas DataFrame.

    Args:
        dataframe (pandas.DataFrame): A data table that contains image path, target class,
            and extra outputs.
        label_id (string): Data frame`s image path label string.
        label_target (string): Data frame`s target class label string.
        label_bbox (tuple[string] or string, optional): Data frame`s label that will
            be used for bbox outputs.
        root (string, optional): Root directory path. Use unless data frame`s column
            contains file folders.
        extension (string, optional): An extension that will be concatenated after
            image file name. Use unless data frame`s column contains extension.
        class_to_idx (dict[str, int], optional): A mapping table that converts class
            label string into integer value. If not given, sorted index value will
            be used as class integer value.
        transforms (callable, optional): Albumentation transform
        loader (callable, optional): A function to load an image given its path.

     Attributes:
        class_to_idx (dict): Dict with items (class_name, class_index).
        samples (list): List of (sample path, class_index) tuples
    """

    def __init__(
            self,
            dataframe: pd.DataFrame,
            label_id: str,
            label_target: str,
            label_bbox: typing.Sequence[str],
            root: typing.Optional[typing.Union[str, os.PathLike]] = None,
            extension: typing.Optional[str] = None,
            class_to_idx: typing.Optional[typing.Dict[typing.Any, int]] = None,
            transforms: typing.Optional[typing.Callable] = None,
            loader: typing.Callable[[str], typing.Any] = default_loader,
    ) -> None:

        super(ImageBboxWithPandas, self).__init__(root, transforms)

        self.loader = loader
        self.label_id = label_id
        self.label_target = label_target
        self.label_bbox = list(label_bbox)
        assert len(self.label_bbox) == 4

        samples = dataframe.copy(deep=True)

        assert extension.startswith('.') or extension is None
        if root is not None:
            root = os.path.expanduser(root)
        if root is not None or extension is not None:
            samples[label_id] = samples[label_id].map(
                (lambda x: os.path.join(root, x + extension or ''))
                if root is not None else (lambda x: x + extension)
            )

        classes = sorted(samples[label_target].unique())
        if class_to_idx is None:
            class_to_idx = {cls_name: i for i, cls_name in enumerate(classes)}
        samples[label_target] = samples[label_target].map(lambda x: class_to_idx[x])

        self.ids = list(samples[label_id].drop_duplicates())

        self.samples = samples
        self.class_to_idx = class_to_idx
        self.num_classes = len(class_to_idx)

    @classmethod
    def split_with_count(
            cls,
            dataframe: pd.DataFrame,
            label_id: str,
            *args, **kwargs
    ):
        dataframe = pd.merge(
            # original dataframe
            dataframe,
            # count dataframe
            dataframe.groupby(label_id).size().reset_index(name='count'),
            # join kwargs
            left_on=label_id, right_on=label_id, how='inner'
        )
        return [
            cls(dataframe[dataframe['count'] == number], label_id, *args, **kwargs)
            for number in dataframe['count'].drop_duplicates().values
        ]

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, index: int) -> ...:
        image_id = self.ids[index]
        rows = self.samples[self.samples[self.label_id] == image_id]

        image = np.array(self.loader(image_id))

        labels = rows[self.label_target]

        boxes = rows[self.label_bbox].values  # x y w h
        boxes[:, 2] = boxes[:, 0] + boxes[:, 2]
        boxes[:, 3] = boxes[:, 1] + boxes[:, 3]

        if (boxes != boxes).any():  # type: ignore
            boxes = []

        sample = {'image': image, 'bboxes': boxes, 'labels': labels}

        if self.transforms:
            sample = self.transforms(**sample)
            if len(sample['bboxes']) > 0:
                sample['bboxes'] = np.array(sample['bboxes'])
                # sample['bboxes'][:, [0, 1, 2, 3]] = sample['bboxes'][:, [1, 0, 3, 2]]  # yxyx: be warning

        image = sample['image']
        labels = torch.tensor(sample['labels'], dtype=torch.int)

        if len(sample['bboxes']) == 0:
            bboxes = torch.stack([torch.tensor(np.zeros((0, 4)))] * len(sample['labels']))
        else:
            bboxes = torch.tensor(sample['bboxes'])

        sample = {'image': image, 'labels': labels, 'boxes': bboxes}

        return sample


class ImageFolder(_ImageFolder):
    __doc__ = _ImageFolder.__doc__

    classes: list = None

    def __init__(
            self,
            root: typing.Union[str, os.PathLike],
            class_to_idx: typing.Optional[typing.Dict[str, int]] = None,
            transform: typing.Optional[typing.Callable] = None,
            target_transform: typing.Optional[typing.Callable] = None,
            loader: typing.Callable[[str], typing.Any] = default_loader,
            is_valid_file: typing.Optional[typing.Callable[[str], bool]] = None,
    ):
        self.class_to_idx = class_to_idx
        super(ImageFolder, self).__init__(root, transform, target_transform, loader, is_valid_file)

    def _find_classes(self, directory: str) -> typing.Tuple[typing.List[str], typing.Dict[str, int]]:
        """
        Finds the class folders in a dataset.

        Args:
            directory (string): Root directory path.

        Returns:
            tuple: (classes, class_to_idx) where classes are relative to (dir), and class_to_idx is a dictionary.

        Ensures:
            No class is a subdirectory of another.
        """
        classes = [d.name for d in os.scandir(directory) if d.is_dir()]  # type: ignore
        classes.sort()
        try:
            class_to_idx = self.class_to_idx
        except AttributeError:
            class_to_idx = None
        if class_to_idx is None:
            class_to_idx = self.class_to_idx or {cls_name: i for i, cls_name in enumerate(classes)}
        return classes, class_to_idx


class DataLoaderChain(IterableDataset):

    __loaders: tuple = ()
    __length: int = 0

    def __init__(self, *loaders):
        self.loaders = loaders

    @classmethod
    def from_datasets(cls, *datasets, **loader_kwargs):
        return cls(*(DataLoader(dataset, **loader_kwargs) for dataset in datasets))

    @property
    def loaders(self):
        return self.__loaders

    @loaders.setter
    def loaders(self, value):
        if not isinstance(value, tuple):
            raise TypeError(str(type(value).__name__))
        self.__loaders = value
        self.__length = sum((map(len, value)))

    @property
    def length(self):
        return self.__length

    def __iter__(self):
        available = [iter(loader) for loader in self.loaders]
        len_available = len(available)
        while True:
            idx = random.randrange(len_available)
            try:
                yield next(available[idx])
            except StopIteration:
                available.pop(idx)
                len_available -= 1
                if len_available == 0:
                    return

    def __len__(self):
        return self.length

    def __getitem__(self, item):
        raise NotImplementedError


__all__ = [
    'pil_loader', 'dicom_loader',
    'ImageWithPandas', 'ImageFolder', 'ImageBboxWithPandas',
    'DataLoaderChain'
]
