# Rigid Body Simulation

Blender bone-physics helper add-on (v1.1.0). Use it in Pose Mode from the **Rigid Body Simulation** panel in the 3D View sidebar.

## Author & Demo

- **Author's Bilibili Profile**: [Visit Profile](https://space.bilibili.com/349903711)
- **Plugin Demo Video**: [Watch on Bilibili](https://www.bilibili.com/video/BV17Fh66WEMV/)

## Installation

Blender 4.2 or newer is required. The GitHub repository contains source code only; installable archives are published on GitHub Releases.

1. Download `RigidBodySimulation-<version>-extension.zip` (recommended for Blender 4.x) or `RigidBodySimulation-<version>-addon.zip`.
2. For an extension package, open **Edit > Preferences > Extensions > Install from Disk**.
3. For an add-on package, open **Edit > Preferences > Add-ons > Install...**.
4. Enable the add-on, select an armature, enter **Pose Mode**, and press `N` to open the panel.

You can also install from source by copying the `RigidBodySimulation` folder to Blender's user `scripts/addons/` directory and enabling it in Preferences. Do not add another nested folder with the same name.

## Use

### Bone-chain rigid bodies

Select a continuous bone chain and make the intended root the active bone. Choose the Skirt, Hair, or Ribbon preset, or adjust radius, mass, damping, and follow settings, then click **Create Rigid-Body Chain**. The root keeps animation control while the other bones are simulated.

### Bone colliders

Select bones that should block the simulated bodies, such as legs or the body. Set the radius and choose Box, Capsule, or Sphere, then click **Add Colliders**. Colliders follow bone and IK motion and can be toggled with **Show/Hide**.

### IK bone chains

Select a continuous chain with the end bone active, then click **Create IK Bone Chain**. The generated box-shaped control can be moved directly to solve the chain.

### Rotation transfer

Select one bone or a chain and click **Add** to copy the parent's local X rotation. Use **Delete** for the selected bones or **Delete All Rotation Transfer** to clear all add-on rotation-transfer constraints on the armature.

### Delete and visibility

The selected-bone delete buttons remove only selected generated content. The **Delete All** section separately removes rigid-body chains, colliders, IK bones, or rotation transfer. Visibility buttons affect the viewport only; physics remains enabled.

The interface follows Blender's language setting: Simplified and Traditional Chinese use Chinese text, and other languages use English.

## License

GPL-3.0-or-later
