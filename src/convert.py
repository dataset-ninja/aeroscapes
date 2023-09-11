import os
from urllib.parse import unquote, urlparse

import numpy as np
import supervisely as sly
from dataset_tools.convert import unpack_if_archive
from supervisely.io.fs import file_exists, get_file_name
from tqdm import tqdm

import src.settings as s


def download_dataset(teamfiles_dir: str) -> str:
    """Use it for large datasets to convert them on the instance"""
    api = sly.Api.from_env()
    team_id = sly.env.team_id()
    storage_dir = sly.app.get_data_dir()

    if isinstance(s.DOWNLOAD_ORIGINAL_URL, str):
        parsed_url = urlparse(s.DOWNLOAD_ORIGINAL_URL)
        file_name_with_ext = os.path.basename(parsed_url.path)
        file_name_with_ext = unquote(file_name_with_ext)

        sly.logger.info(f"Start unpacking archive '{file_name_with_ext}'...")
        local_path = os.path.join(storage_dir, file_name_with_ext)
        teamfiles_path = os.path.join(teamfiles_dir, file_name_with_ext)

        fsize = api.file.get_directory_size(team_id, teamfiles_dir)
        with tqdm(desc=f"Downloading '{file_name_with_ext}' to buffer...", total=fsize) as pbar:
            api.file.download(team_id, teamfiles_path, local_path, progress_cb=pbar)
        dataset_path = unpack_if_archive(local_path)

    if isinstance(s.DOWNLOAD_ORIGINAL_URL, dict):
        for file_name_with_ext, url in s.DOWNLOAD_ORIGINAL_URL.items():
            local_path = os.path.join(storage_dir, file_name_with_ext)
            teamfiles_path = os.path.join(teamfiles_dir, file_name_with_ext)

            if not os.path.exists(get_file_name(local_path)):
                fsize = api.file.get_directory_size(team_id, teamfiles_dir)
                with tqdm(
                    desc=f"Downloading '{file_name_with_ext}' to buffer...",
                    total=fsize,
                    unit="B",
                    unit_scale=True,
                ) as pbar:
                    api.file.download(team_id, teamfiles_path, local_path, progress_cb=pbar)

                sly.logger.info(f"Start unpacking archive '{file_name_with_ext}'...")
                unpack_if_archive(local_path)
            else:
                sly.logger.info(
                    f"Archive '{file_name_with_ext}' was already unpacked to '{os.path.join(storage_dir, get_file_name(file_name_with_ext))}'. Skipping..."
                )

        dataset_path = storage_dir
    return dataset_path


def count_files(path, extension):
    count = 0
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith(extension):
                count += 1
    return count


def convert_and_upload_supervisely_project(
    api: sly.Api, workspace_id: int, project_name: str
) -> sly.ProjectInfo:
    ### Function should read local dataset and upload it to Supervisely project, then return project info.###
    # raise NotImplementedError("The converter should be implemented manually.")

    project_name = "Aeroscapes"
    dataset_path = "aeroscapes"
    img_path = os.path.join(dataset_path, "JPEGImages")
    segmentation_path = os.path.join(dataset_path, "Visualizations")
    batch_size = 50

    classes = {
        "Background": (0, 0, 0),
        "Person": (192, 128, 128),
        "Bike": (0, 128, 0),
        "Car": (128, 128, 128),
        "Drone": (128, 0, 0),
        "Boat": (0, 0, 128),
        "Animal": (192, 0, 128),
        "Obstacle": (192, 0, 0),
        "Construction": (192, 128, 0),
        "Vegetation": (0, 64, 0),
        "Road": (128, 128, 0),
        "Sky": (0, 128, 128),
    }

    train_set = os.path.join(dataset_path, "ImageSets", "trn.txt")
    val_set = os.path.join(dataset_path, "ImageSets", "val.txt")

    project_dict = {"val": val_set, "train": train_set}

    for ds in project_dict:
        with open(project_dict[ds]) as f:
            lines = f.readlines()
            images = [line[: line.find("\n")] + ".jpg" for line in lines]
            project_dict[ds] = images

    def get_unique_colors(img):
        unique_colors = []
        img = img.astype(np.int32)
        h, w = img.shape[:2]
        colhash = img[:, :, 0] * 256 * 256 + img[:, :, 1] * 256 + img[:, :, 2]
        unq, unq_inv, unq_cnt = np.unique(colhash, return_inverse=True, return_counts=True)
        indxs = np.split(np.argsort(unq_inv), np.cumsum(unq_cnt[:-1]))
        col2indx = {unq[i]: indxs[i][0] for i in range(len(unq))}
        for col, indx in col2indx.items():
            if col != 0:
                unique_colors.append((col // (256**2), (col // 256) % 256, col % 256))
        unique_colors.append((0, 0, 0))
        return unique_colors

    def get_key(dict, value):
        for k, v in dict.items():
            if v == value:
                return k

    def create_ann(image_path):
        labels = []
        image_name = get_file_name(os.path.basename(image_path))
        image_np = sly.imaging.image.read(image_path)[:, :, 0]
        img_height = image_np.shape[0]
        img_wight = image_np.shape[1]
        mask_path = os.path.join(segmentation_path, f"{image_name}.png")
        if file_exists(mask_path):
            mask_np = sly.imaging.image.read(mask_path)
            if len(np.unique(mask_np)) != 1:
                uniq_color = get_unique_colors(mask_np)
                for color in uniq_color:
                    obj_class = meta.get_obj_class(get_key(classes, color))
                    mask = np.all(mask_np == color, axis=2)
                    curr_bitmap = sly.Bitmap(mask)
                    curr_label = sly.Label(curr_bitmap, obj_class)
                    labels.append(curr_label)
        return sly.Annotation(img_size=(img_height, img_wight), labels=labels)

    obj_classes = [
        sly.ObjClass(label_name, sly.Bitmap, classes[label_name]) for label_name in classes
    ]
    project = api.project.create(workspace_id, project_name, change_name_if_conflict=True)
    meta = sly.ProjectMeta(obj_classes=obj_classes)
    api.project.update_meta(project.id, meta.to_json())

    dataset = dataset_val = api.dataset.create(project.id, "val", change_name_if_conflict=True)
    dataset_train = api.dataset.create(project.id, "train", change_name_if_conflict=True)

    progress = sly.Progress(
        "Create dataset {}".format(dataset.name),
        len(project_dict["train"]) + len(project_dict["val"]),
    )

    for ds in project_dict:
        dataset = dataset_train if ds == "train" else dataset_val
        image_names = project_dict[ds]
        for img_names_batch in sly.batched(image_names, batch_size=batch_size):
            img_pathes_batch = [os.path.join(img_path, img_name) for img_name in img_names_batch]
            img_infos = api.image.upload_paths(dataset.id, img_names_batch, img_pathes_batch)
            img_ids = [im_info.id for im_info in img_infos]
            anns_batch = [create_ann(image_path) for image_path in img_pathes_batch]
            api.annotation.upload_anns(img_ids, anns_batch)
            progress.iters_done_report(len(img_names_batch))

            # sly.logger.info('Deleting temporary app storage files...')
            # shutil.rmtree(storage_dir)

    return project
