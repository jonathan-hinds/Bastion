# Unity Mobile WebGL Setup

This project is configured around a portrait 9:16 mobile browser target for an 8-bit inspired tower-defense game played with the phone upright.

## Baseline

- Unity version: 6000.0.37f1.
- Render pipeline: Universal Render Pipeline with the 2D Renderer.
- Browser build target: WebGL.
- Logical pixel reference: 180 x 320.
- Default WebGL canvas: 540 x 960.
- Pixel art import: point filtering, no mipmaps, uncompressed sprites, 16 pixels per unit.
- Runtime target: 60 FPS, no VSync, no device sleep.

## Installed Core Packages

- 2D feature set, including Pixel Perfect, Tilemap Extras, Aseprite Importer, PSD Importer, SpriteShape, and 2D Animation.
- Input System.
- Cinemachine for future 2D camera follow, shake, and confiner work.
- Test Framework.

## Project Layout

- `Assets/_Breach/Art/Sprites`: standalone sprite art.
- `Assets/_Breach/Art/Tiles`: tile and tileset source art.
- `Assets/_Breach/Audio`: music and sound effects.
- `Assets/_Breach/Data`: JSON, ScriptableObjects, and tuning data.
- `Assets/_Breach/Prefabs`: reusable gameplay prefabs.
- `Assets/_Breach/Scenes`: production scenes.
- `Assets/_Breach/Scripts`: runtime and editor code.
- `Assets/_Breach/Settings`: presets and project-level Unity assets.
- `Assets/_Breach/UI`: UI documents, prefabs, and art.

## Reapplying Setup

Use `Breach > Apply Mobile WebGL Pixel Setup` in the Unity menu after major package or settings changes. The command reapplies the player, WebGL, camera, tilemap, physics, quality, and pixel-art import defaults.

## Release Note

The current WebGL setup keeps explicit exception support on while the game is still early. Before a public release build, test a build with WebGL exceptions set to `None` and managed stripping set to `High` for smaller downloads.
