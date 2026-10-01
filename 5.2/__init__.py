"""Rigid Body Simulation for selected armature bone chains."""

bl_info = {
    "name": "Rigid Body Simulation",
    "author": "ayacyann",
    "version": (1, 1, 3),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > Rigid Body Simulation",
    "description": "Create rigid-body simulation proxies for selected pose bones",
    "category": "Animation",
}


if __package__:
    from .modules import *
else:
    # Keep direct source-file loading used by older validation scripts working.
    # Blender normally imports this file as the RigidBodySimulation package.
    import importlib.util as _importlib_util
    import pathlib as _pathlib
    import sys as _sys

    _package_root = _pathlib.Path(__file__).resolve().parent
    _module_name = f"{__name__}.modules"
    _module_spec = _importlib_util.spec_from_file_location(
        _module_name,
        _package_root / "modules" / "__init__.py",
        submodule_search_locations=[str(_package_root / "modules")],
    )
    _module = _importlib_util.module_from_spec(_module_spec)
    _sys.modules[_module_name] = _module
    _module_spec.loader.exec_module(_module)
    globals().update(
        {name: value for name, value in vars(_module).items() if not name.startswith("__")}
    )


if __name__ == "__main__":
    register()
