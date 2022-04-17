from typing import Optional, Dict, cast, Tuple

from rich.prompt import Prompt

from exegol.console.ExegolPrompt import Confirm
from exegol.console.TUI import ExegolTUI
from exegol.console.cli.ParametersManager import ParametersManager
from exegol.exceptions.ExegolExceptions import ObjectNotFound
from exegol.model.ExegolImage import ExegolImage
from exegol.utils.ConstantConfig import ConstantConfig
from exegol.utils.DockerUtils import DockerUtils
from exegol.utils.ExeLog import logger, console, ExeLog
from exegol.utils.GitUtils import GitUtils
from exegol.utils.UserConfig import UserConfig


class UpdateManager:
    """Procedure class for updating the exegol tool and docker images"""
    __git: Optional[GitUtils] = None
    __git_source: Optional[GitUtils] = None
    __git_resources: Optional[GitUtils] = None

    @classmethod
    def __getGit(cls) -> GitUtils:
        """GitUtils local singleton getter"""
        if cls.__git is None:
            cls.__git = GitUtils()
        return cls.__git

    @classmethod
    def __getSourceGit(cls) -> GitUtils:
        """GitUtils source submodule singleton getter"""
        # Be sure that submodule are init first
        cls.__getGit()
        if cls.__git_source is None:
            cls.__git_source = GitUtils(ConstantConfig.src_root_path_obj / "exegol-docker-build", "image")
        return cls.__git_source

    @classmethod
    def __getResourcesGit(cls):
        """GitUtils resource repo/submodule singleton getter"""
        # Be sure that submodule are init first
        cls.__getGit()
        if cls.__git_resources is None:
            cls.__git_resources = GitUtils(UserConfig().exegol_resources_path, "resources", "")
            if not cls.__git_resources.isAvailable:
                cls.__init_resources_repo()
        return cls.__git_resources

    @classmethod
    def __init_resources_repo(cls):
        if Confirm("Do you want to download exegol resources?", True):
            cls.__git_resources.clone(ConstantConfig.EXEGOL_RESOURCES_REPO)

    @classmethod
    def updateImage(cls, tag: Optional[str] = None, install_mode: bool = False) -> Optional[ExegolImage]:
        """User procedure to build/pull docker image"""
        # List Images
        image_args = ParametersManager().imagetag
        # Select image
        if image_args is not None and tag is None:
            tag = image_args
        if tag is None:
            try:
                # Interactive selection
                selected_image = ExegolTUI.selectFromTable(DockerUtils.listImages(),
                                                           object_type=ExegolImage,
                                                           allow_None=install_mode)
            except IndexError:
                # No images are available
                if install_mode:
                    # If no image are available in install mode,
                    # either the user does not have internet,
                    # or the image repository has been changed and no docker image is available
                    logger.critical("Exegol can't be installed offline")
                return None
        else:
            try:
                # Find image by name
                selected_image = DockerUtils.getImage(tag)
            except ObjectNotFound:
                # If the image do not exist, ask to build it
                return cls.__askToBuild(tag)

        if selected_image is not None and type(selected_image) is ExegolImage:
            # Update existing ExegolImage
            if DockerUtils.downloadImage(selected_image, install_mode):
                sync_result = None
                # Name comparison allow detecting images without version tag
                if not selected_image.isVersionSpecific() and selected_image.getName() != selected_image.getLatestVersionName():
                    with console.status(f"Synchronizing version tag information. Please wait.", spinner_style="blue"):
                        # Download associated version tag.
                        sync_result = DockerUtils.downloadVersionTag(selected_image)
                    # Detect if an error have been triggered during the download
                    if type(sync_result) is str:
                        logger.error(f"Error while downloading version tag, {sync_result}")
                        sync_result = None
                # if version tag have been successfully download, returning ExegolImage from docker response
                if sync_result is not None and type(sync_result) is ExegolImage:
                    return sync_result
                return DockerUtils.getInstalledImage(selected_image.getName())
        elif type(selected_image) is str:
            # Build a new image using TUI selected name, confirmation has already been requested by TUI
            return cls.buildAndLoad(selected_image)
        else:
            # Unknown use case
            logger.critical(f"Unknown selected image type: {type(selected_image)}. Exiting.")
        return cast(Optional[ExegolImage], selected_image)

    @classmethod
    def __askToBuild(cls, tag: str) -> Optional[ExegolImage]:
        """Build confirmation process and image building"""
        # Need confirmation from the user before starting building.
        if ParametersManager().build_profile is not None or \
                Confirm("Do you want to build locally a custom image?", default=False):
            return cls.buildAndLoad(tag)
        return None

    @classmethod
    def updateWrapper(cls) -> bool:
        """Update wrapper source code from git"""
        return cls.__updateGit(cls.__getGit())

    @classmethod
    def updateImageSource(cls) -> bool:
        """Update image source code from git submodule"""
        return cls.__updateGit(cls.__getSourceGit())

    @classmethod
    def updateResources(cls) -> bool:
        """Update Exegol-resources from git (submodule)"""
        return cls.__updateGit(cls.__getResourcesGit())

    @staticmethod
    def __updateGit(gitUtils: GitUtils) -> bool:
        """User procedure to update local git repository"""
        if not gitUtils.isAvailable:
            logger.empty_line()
            return False
        logger.info(f"Updating Exegol {gitUtils.getName()} {gitUtils.getSubject()}")
        # Check if pending change -> cancel
        if not gitUtils.safeCheck():
            logger.error("Aborting git update.")
            logger.empty_line()
            return False
        current_branch = gitUtils.getCurrentBranch()
        if current_branch is None:
            logger.warning("HEAD is detached. Please checkout to an existing branch.")
            current_branch = "unknown"
        if logger.isEnabledFor(ExeLog.VERBOSE) or current_branch not in ["master", "main"]:
            logger.info(f"Current git branch : {current_branch}")
            # List & Select git branch
            default_choice = current_branch if current_branch != 'unknown' else None
            selected_branch = cast(str, ExegolTUI.selectFromList(gitUtils.listBranch(),
                                                                 subject="a git branch",
                                                                 title="Branch",
                                                                 default=default_choice))
            # Checkout new branch
            if selected_branch is not None and selected_branch != current_branch:
                gitUtils.checkout(selected_branch)
        # git pull
        gitUtils.update()
        logger.empty_line()
        return True

    @classmethod
    def buildSource(cls, build_name: Optional[str] = None) -> str:
        """build user process :
        Ask user is he want to update the git source (to get new& updated build profiles),
        User choice a build name (if not supplied)
        User select a build profile
        Start docker image building
        Return the name of the built image"""
        # Ask to update git
        try:
            if cls.__getGit().isAvailable and not cls.__getGit().isUpToDate() and Confirm(
                    "Do you want to update git (in order to update local build profiles)?",
                    default=True):
                cls.updateImageSource()
        except AssertionError:
            # Catch None git object assertions
            logger.warning("Git update is not available. Skipping.")
        # Choose tag name
        blacklisted_build_name = ["stable", "full"]
        while build_name is None or build_name in blacklisted_build_name:
            if build_name is not None:
                logger.error("This name is reserved and cannot be used for local build. Please choose another one.")
            build_name = Prompt.ask("[blue][?][/blue] Choice a name for your build",
                                    default="local")
        # Choose dockerfile
        profiles = cls.listBuildProfiles()
        build_profile: Optional[str] = ParametersManager().build_profile
        build_dockerfile: Optional[str] = None
        if build_profile is not None:
            build_dockerfile = profiles.get(build_profile)
            if build_dockerfile is None:
                logger.error(f"Build profile {build_profile} not found.")
        if build_dockerfile is None:
            build_profile, build_dockerfile = cast(Tuple[str, str], ExegolTUI.selectFromList(profiles,
                                                                                             subject="a build profile",
                                                                                             title="Profile"))
        logger.debug(f"Using {build_profile} build profile ({build_dockerfile})")
        # Docker Build
        DockerUtils.buildImage(build_name, build_profile, build_dockerfile)
        return build_name

    @classmethod
    def buildAndLoad(cls, tag: str):
        """Build an image and load it"""
        build_name = cls.buildSource(tag)
        return DockerUtils.getInstalledImage(build_name)

    @classmethod
    def listBuildProfiles(cls) -> Dict:
        """List every build profiles available locally
        Return a dict of options {"key = profile name": "value = dockerfile full name"}"""
        # Default stable profile
        profiles = {"full": "Dockerfile"}
        # List file *.dockerfile is the build context directory
        logger.debug(f"Loading build profile from {ConstantConfig.build_context_path}")
        docker_files = list(ConstantConfig.build_context_path_obj.glob("*.dockerfile"))
        for file in docker_files:
            # Convert every file to the dict format
            filename = file.name
            profile_name = filename.replace(".dockerfile", "")
            profiles[profile_name] = filename
        logger.debug(f"List docker build profiles : {profiles}")
        return profiles
