"""v1.1.3 feature module; class bodies are kept behavior-compatible."""
from .core import *

class AYARBS_ChainPreset(bpy.types.PropertyGroup):
    """A user preset stored in the .blend scene."""

    preset_name: StringProperty(name="预设名称", default="", description="Custom Preset Name")
    chain_radius: FloatProperty(default=0.01, min=0.001, unit="LENGTH", description="Body Radius")
    body_length_scale: FloatProperty(default=0.85, min=0.1, max=1.0, description="Body Length Scale")
    mass: FloatProperty(default=0.5, min=0.001, description="Mass")
    linear_damping: FloatProperty(default=0.35, min=0.0, max=1.0, description="Linear Damping")
    angular_damping: FloatProperty(default=0.45, min=0.0, max=1.0, description="Angular Damping")
    follow_strength: FloatProperty(default=0.5, min=0.0, max=1.0, description="Root Follow Strength")
    spring_stiffness: FloatProperty(default=10.0, min=1.0, max=100.0, description="Chain Spring Stiffness")
    spring_damping: FloatProperty(default=7.5, min=1.0, max=100.0, description="Chain Spring Damping")

class AYARBS_Settings(bpy.types.PropertyGroup):
    chain_radius: FloatProperty(name="刚体半径", default=0.01, min=0.001, unit="LENGTH", description="Body Radius")
    body_length_scale: FloatProperty(name="刚体长度比例", default=0.85, min=0.1, max=1.0, description="Body Length Scale")
    # The first menu item is the built-in Default preset; matching the property
    # defaults keeps the initial panel selection and visible values consistent.
    mass: FloatProperty(name="质量", default=0.5, min=0.001, description="Mass")
    linear_damping: FloatProperty(name="线性阻尼", default=0.85, min=0.0, max=1.0, description="Linear Damping")
    angular_damping: FloatProperty(name="角阻尼", default=0.45, min=0.0, max=1.0, description="Angular Damping")
    follow_strength: FloatProperty(name="根骨骼跟随强度", default=0.5, min=0.0, max=1.0, description="Root Follow Strength")
    damped_follow_strength: FloatProperty(name="阻尼跟随强度", default=0.5, min=0.0, max=1.0, description="Damped Follow Strength")
    spring_stiffness: FloatProperty(name="链条弹簧刚度", default=10.0, min=1.0, max=100.0, description="Chain Spring Stiffness")
    spring_damping: FloatProperty(name="链条弹簧阻尼", default=7.5, min=1.0, max=100.0, description="Chain Spring Damping")
    collider_radius: FloatProperty(name="碰撞体半径", default=0.12, min=0.001, unit="LENGTH", description="Collider Radius")
    collider_shape: EnumProperty(
        name="碰撞形状",
        items=_collider_shape_items,
        description="Collider Shape",
        # EnumProperty callbacks use an integer item index for the default.
        # Keep CAPSULE as the second entry to preserve the previous default.
        default=1,
    )
    ik_shape_size: FloatProperty(
        name="IK自定义物体缩放",
        default=0.75,
        min=0.05,
        max=2.0,
        description="IK custom shape scale; the same value is applied to X, Y, and Z.",
    )
    colliders_visible: BoolProperty(name="显示碰撞体", default=True, description="Show Colliders")
    bodies_visible: BoolProperty(name="显示刚体代理", default=True, description="Show Body Proxies")
    chain_preset: EnumProperty(
        name="刚体链预设",
        items=_chain_preset_items,
        description="Rigid-Body Chain Preset",
        # Blender requires an integer default when ``items`` is a callback.
        # The callback returns DEFAULT as its first item, so it remains the
        # initial selection without using an invalid string default.
        update=_apply_chain_preset,
    )
    chain_preset_name: StringProperty(name="自定义预设名称", default="", description="Custom Preset Name")
    chain_presets: CollectionProperty(type=AYARBS_ChainPreset)
