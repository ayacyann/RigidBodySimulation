# Rigid Body Simulation

> Blender bone-physics helper add-on · **v1.1.1**
>
> Build editable rigid-body chains, bone colliders, continuous or two-island IK bindings, and local X-axis rotation-transfer helpers directly in Pose Mode.

![Blender bone rigid-body simulation](images/骨骼刚体头发模拟后.jpg)

## Requirements and installation

- Blender **4.2 or newer**.
- An editable Armature.
- Enter **Pose Mode**, press `N` in the 3D View, and open the **Rigid Body Simulation** tab.

Download `RigidBodySimulation-<version>-extension.zip` (recommended for Blender 4.x) or `RigidBodySimulation-<version>-addon.zip`. Install an extension from **Edit > Preferences > Extensions > Install from Disk**, or install the add-on from **Edit > Preferences > Add-ons > Install...**. Source installation is also supported by copying the `RigidBodySimulation` folder into Blender's user `scripts/addons/` directory.

## Contents

- [1. Rigid-body bone chains](#1-rigid-body-bone-chains)
- [2. Bone colliders](#2-bone-colliders)
- [3. IK binding](#3-ik-binding)
- [4. Rotation transfer](#4-rotation-transfer)
- [Visibility, deletion, and tips](#visibility-deletion-and-tips)

## 1. Rigid-body bone chains

Select a continuous parent-child chain, make the root active, choose a preset or tune the parameters, and click **Create Rigid-Body Chain**. The root remains animation-driven while the other selected bones receive generated rigid-body proxies and chain constraints.

### Hair preset

The Hair preset is a balanced starting point for longer hair strands. Increase damping if the strand jitters during fast motion.

<table>
<tr><td><img src="images/骨骼刚体头发选中.jpg" alt="Select hair chain" width="100%"><br><sub>Select hair chain</sub></td><td><img src="images/骨骼刚体头发创建.jpg" alt="Create hair chain" width="100%"><br><sub>Create chain</sub></td></tr>
<tr><td><img src="images/骨骼刚体头发模拟前.jpg" alt="Hair before simulation" width="100%"><br><sub>Before simulation</sub></td><td><img src="images/骨骼刚体头发模拟后.jpg" alt="Hair after simulation" width="100%"><br><sub>After simulation</sub></td></tr>
</table>

### Ribbon preset

The Ribbon preset is lighter and more flexible. It works well for straps, ribbons, and narrow cloth strips; raise spring damping when the ribbon oscillates too much.

<table>
<tr><td><img src="images/骨骼刚体飘带选中.jpg" alt="Select ribbon chain" width="100%"><br><sub>Select ribbon chain</sub></td><td><img src="images/骨骼刚体飘带创建.jpg" alt="Create ribbon chain" width="100%"><br><sub>Create chain</sub></td></tr>
<tr><td><img src="images/骨骼刚体飘带模拟前.jpg" alt="Ribbon before simulation" width="100%"><br><sub>Before simulation</sub></td><td><img src="images/骨骼刚体飘带模拟后.jpg" alt="Ribbon after simulation" width="100%"><br><sub>After simulation</sub></td></tr>
</table>

### Skirt preset

The Skirt preset favors stable root following. Tune root-follow strength for the desired relationship between the character animation and the simulated hem.

<table>
<tr><td><img src="images/骨骼刚体裙摆选中.jpg" alt="Select skirt chain" width="100%"><br><sub>Select skirt chain</sub></td><td><img src="images/骨骼刚体裙摆创建.jpg" alt="Create skirt chain" width="100%"><br><sub>Create chain</sub></td></tr>
<tr><td><img src="images/骨骼刚体裙摆模拟前.jpg" alt="Skirt before simulation" width="100%"><br><sub>Before simulation</sub></td><td><img src="images/骨骼刚体裙摆模拟后.jpg" alt="Skirt after simulation" width="100%"><br><sub>After simulation</sub></td></tr>
</table>

| Parameter | Purpose |
| --- | --- |
| Body radius / length scale | Shape and coverage of each proxy |
| Mass | Inertia of dynamic bodies |
| Linear / angular damping | Suppress translation and rotation jitter |
| Root follow strength | Keep the chain attached to the animated root |
| Spring stiffness / damping | Control recovery and oscillation between neighboring bodies |

The three built-in presets can be adjusted after selection. Use the preset name field and **Add** to save a scene-local custom preset; **Delete Preset** removes a saved user preset.

## 2. Bone colliders

Colliders are passive obstacles for simulated hair, ribbons, skirts, and clothing. Select one or more bones, set **Collider Radius**, choose **Box**, **Capsule**, or **Sphere**, and click **Add Colliders**. Repeating the operation replaces the generated collider for the same bone.

<table><tr>
<td><img src="images/创建骨骼碰撞体.jpg" alt="Create one bone collider" width="100%"><br><sub>Single collider</sub></td>
<td><img src="images/创建所有骨骼碰撞体.jpg" alt="Create colliders for selected bones" width="100%"><br><sub>Selected bones</sub></td>
<td><img src="images/骨骼碰撞体与刚体的碰撞模拟.jpg" alt="Collider and rigid-body simulation" width="100%"><br><sub>Collision result</sub></td>
</tr></table>

**Show/Hide Colliders** only changes viewport/render visibility; the physics collision remains active. Use **Delete Selected Bone Colliders** or **Delete All > Delete Bone Colliders** to remove generated colliders.

## 3. IK binding

The IK module creates Blender-native IK constraints and a movable custom control. The control is intended as a translation handle, with rotation and scale locked by default.

### Continuous-chain mode

Select a continuous chain with its end bone active and click **Create IK Bone Chain**. A generated IK control is placed at the active end and the end bone receives the solver.

### Two-island mode

Select an existing IK control as the active bone and select a separate chain to solve. The active island must contain exactly one bone; the other island must be a connected, unbranched chain. The active bone is reused as the IK target, the terminal bone of the chain receives the constraint, and chain length equals the number of chain bones. Selections with more than two islands, a multi-bone active island, or a branched chain are rejected.

<table>
<tr><td><img src="images/非连续ik链选择手臂.jpg" alt="Select two-island arm IK" width="100%"><br><sub>Arm selection</sub></td><td><img src="images/非连续ik链创建手臂.jpg" alt="Create two-island arm IK" width="100%"><br><sub>Arm IK result</sub></td></tr>
<tr><td><img src="images/非连续ik链选择脚部.jpg" alt="Select two-island foot IK" width="100%"><br><sub>Foot selection</sub></td><td><img src="images/非连续ik链创建脚部.jpg" alt="Create two-island foot IK" width="100%"><br><sub>Foot IK result</sub></td></tr>
</table>

**IK Custom Object Scale** is one user-editable value, defaulting to `0.75`. The same value is written to the custom object's X, Y, and Z scale. The custom-shape translation and IK pose offset are reset to zero when the IK is created.

## 4. Rotation transfer

Rotation transfer is useful for fingers and helper chains that should inherit a parent's local X rotation. Select one bone or a continuous chain, make the intended starting bone active, and click **Add**.

<table><tr>
<td><img src="images/旋转传递手指选择.jpg" alt="Select finger rotation-transfer chain" width="50%"><br><sub>Select chain</sub></td>
<td><img src="images/旋转传递手指创建与使用.jpg" alt="Use finger rotation transfer" width="50%"><br><sub>Create and use</sub></td>
</tr></table>

The add-on creates `Copy Rotation` constraints that use only the local X axis. Re-adding replaces only this add-on's generated transfer constraints and leaves manually authored constraints untouched. **Delete** removes transfers from selected bones; **Delete All Rotation Transfer** clears all transfers generated by this add-on on the active armature.

## Visibility, deletion, and tips

- Generated objects use the `RBS_` prefix and are organized under `RBS_Physics`, `RBS_Bodies`, `RBS_Colliders`, and `RBS_IK_Controls`.
- **Show/Hide Body Proxies** and **Show/Hide Colliders** affect display only; use the delete operators to remove simulation content.
- Create the rigid-body chain first, add colliders second, and add IK or rotation transfer after the rig structure is stable.
- Keep chain selections linear. Split branches into separate operations.
- Increase damping when a chain jitters; reduce mass or increase collider radius when the result feels too loose or penetrates nearby geometry.
- To rebuild an IK binding, delete the current IK first, then select the desired chain and create it again.

The interface follows Blender's language setting: Simplified and Traditional Chinese use Chinese labels, while other languages use English.

## License

GPL-3.0-or-later
