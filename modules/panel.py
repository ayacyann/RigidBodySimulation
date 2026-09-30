"""v1.1.2 feature module; class bodies are kept behavior-compatible."""
from .core import *

class RBS_PT_panel(bpy.types.Panel):
    bl_label = "Rigid Body Simulation"
    bl_idname = "RBS_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "刚体模拟"

    @classmethod
    def poll(cls, context):
        # The sidebar is useful for inspecting settings and managing generated
        # data in Object/Edit/Pose mode. Individual creation operators keep
        # their own Pose-mode polls, so showing the panel does not make an
        # invalid operation executable in another mode.
        obj = getattr(context, "object", None)
        return obj is not None and obj.type == "ARMATURE"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.rbs_settings
        col = layout.column(align=True)
        col.label(text=_ui_text(context, "骨骼链刚体", "Rigid-Body Chain"))
        preset_box = col.box()
        preset_box.label(text=_ui_text(context, "刚体链参数预设", "Rigid-Body Chain Presets"))
        preset_box.prop(settings, "chain_preset", text=_ui_text(context, "选择预设", "Preset"))
        preset_row = preset_box.row(align=True)
        preset_row.prop(settings, "chain_preset_name", text=_ui_text(context, "名称", "Name"))
        preset_row.operator("rbs.add_chain_preset", text=_ui_text(context, "添加", "Add"), icon="ADD")
        delete_row = preset_box.row(align=True)
        delete_row.operator(
            "rbs.remove_chain_preset",
            text=_ui_text(context, "删除预设", "Delete Preset"),
            icon="TRASH",
        )
        delete_row.enabled = _selected_user_preset_index(settings) >= 0
        col.prop(settings, "chain_radius", text=_ui_text(context, "刚体半径", "Body Radius"))
        col.prop(settings, "body_length_scale", text=_ui_text(context, "刚体长度比例", "Body Length Scale"))
        col.prop(settings, "mass", text=_ui_text(context, "质量", "Mass"))
        col.prop(settings, "linear_damping", text=_ui_text(context, "线性阻尼", "Linear Damping"))
        col.prop(settings, "angular_damping", text=_ui_text(context, "角阻尼", "Angular Damping"))
        col.prop(settings, "follow_strength", text=_ui_text(context, "根骨骼跟随强度", "Root Follow Strength"))
        col.prop(settings, "spring_stiffness", text=_ui_text(context, "链条弹簧刚度", "Chain Spring Stiffness"))
        col.prop(settings, "spring_damping", text=_ui_text(context, "链条弹簧阻尼", "Chain Spring Damping"))
        row = col.row(align=True)
        row.operator("rbs.build_chain", text=_ui_text(context, "创建骨骼链刚体", "Create Rigid-Body Chain"), icon="PHYSICS")
        row.operator(
            "rbs.reset_body_positions",
            text=_ui_text(context, "重置刚体位置", "Reset Body Positions"),
            icon="FILE_REFRESH",
        )
        damped_box = col.box()
        damped_box.label(text=_ui_text(context, "阻尼跟随", "Damped Follow"))
        damped_box.prop(settings, "damped_follow_strength", text=_ui_text(context, "跟随强度", "Follow Strength"))
        damped_box.operator("rbs.create_damped_follow", text=_ui_text(context, "创建/覆盖阻尼跟随", "Create/Override Damped Follow"), icon="CONSTRAINT_BONE")
        row = col.row(align=True)
        row.operator(
            "rbs.toggle_body_proxies",
            text=_ui_text(context, "代理显示/隐藏", "Show/Hide Proxies"),
            icon="HIDE_OFF" if settings.bodies_visible else "HIDE_ON",
        )
        col.operator(
            "rbs.remove_chain_selected",
            text=_ui_text(context, "删除选中骨骼刚体", "Delete Selected Bone Bodies"),
            icon="TRASH",
        )
        col.separator()
        col.label(text=_ui_text(context, "IK骨骼链", "IK Bone Chain"))
        col.prop(
            settings,
            "ik_shape_size",
            text=_ui_text(context, "IK自定义物体缩放", "IK Custom Object Scale"),
        )
        col.operator("rbs.create_ik_chain", text=_ui_text(context, "创建IK骨骼链", "Create IK Bone Chain"), icon="CONSTRAINT_BONE")
        col.operator("rbs.remove_ik_selected", text=_ui_text(context, "删除当前骨骼IK", "Delete Selected Bone IK"), icon="TRASH")
        col.separator()
        col.label(text=_ui_text(context, "骨骼碰撞体", "Bone Colliders"))
        col.prop(settings, "collider_radius", text=_ui_text(context, "碰撞体半径", "Collider Radius"))
        col.prop(settings, "collider_shape", text=_ui_text(context, "碰撞形状", "Collider Shape"))
        row = col.row(align=True)
        row.operator("rbs.add_colliders", text=_ui_text(context, "添加碰撞体", "Add Colliders"), icon="MESH_UVSPHERE")
        row.operator("rbs.toggle_colliders", text=_ui_text(context, "显示/隐藏", "Show/Hide"), icon="HIDE_OFF" if settings.colliders_visible else "HIDE_ON")
        col.operator("rbs.remove_colliders_selected", text=_ui_text(context, "删除当前骨骼碰撞体", "Delete Selected Bone Colliders"), icon="TRASH")
        col.separator()
        col.label(text=_ui_text(context, "旋转传递", "Rotation Transfer"))
        row = col.row(align=True)
        row.operator("rbs.add_rotation_transfer", text=_ui_text(context, "添加", "Add"), icon="CONSTRAINT_BONE")
        row.operator("rbs.remove_rotation_transfer", text=_ui_text(context, "删除", "Delete"), icon="TRASH")
        col.separator()
        col.label(text=_ui_text(context, "删除所有", "Delete All"))
        row = col.row(align=True)
        row.operator("rbs.remove_chain", text=_ui_text(context, "删除刚体链", "Delete Rigid-Body Chains"), icon="TRASH")
        row.operator("rbs.remove_colliders", text=_ui_text(context, "删除碰撞体", "Delete Bone Colliders"), icon="TRASH")
        row = col.row(align=True)
        row.operator("rbs.remove_ik", text=_ui_text(context, "删除所有IK骨骼", "Delete All IK Bones"), icon="TRASH")
        row.operator("rbs.remove_all_rotation_transfer", text=_ui_text(context, "删除所有旋转传递", "Delete All Rotation Transfer"), icon="TRASH")

