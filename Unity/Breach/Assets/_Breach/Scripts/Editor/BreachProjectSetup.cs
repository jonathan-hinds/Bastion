using System;
using System.IO;
using System.Linq;
using Breach.Foundation;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Presets;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Tilemaps;

namespace Breach.Editor
{
    public static class BreachProjectSetup
    {
        private const string BootstrapScenePath = "Assets/_Breach/Scenes/Bootstrap.unity";
        private const string PixelArtPresetPath = "Assets/_Breach/Settings/PixelArtTextureImporter.preset";
        private const string WebTemplate = "PROJECT:BreachMobilePixel";
        private const int WebCanvasWidth = 540;
        private const int WebCanvasHeight = 960;

        [MenuItem("Breach/Apply Mobile WebGL Pixel Setup")]
        public static void Apply()
        {
            EnsureFolders();
            ConfigurePlayerSettings();
            ConfigureQualitySettings();
            ConfigurePhysicsSettings();
            ConfigureScene();
            EnsurePixelArtPreset();
            EnsurePortraitGameViewSize();
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log("Breach mobile WebGL pixel setup applied.");
        }

        private static void EnsureFolders()
        {
            string[] folders =
            {
                "Assets/_Breach",
                "Assets/_Breach/Art",
                "Assets/_Breach/Art/Sprites",
                "Assets/_Breach/Art/Tiles",
                "Assets/_Breach/Audio",
                "Assets/_Breach/Data",
                "Assets/_Breach/Documentation",
                "Assets/_Breach/Prefabs",
                "Assets/_Breach/Scenes",
                "Assets/_Breach/Scripts",
                "Assets/_Breach/Scripts/Editor",
                "Assets/_Breach/Scripts/Runtime",
                "Assets/_Breach/Settings",
                "Assets/_Breach/UI",
                "Assets/WebGLTemplates",
                "Assets/WebGLTemplates/BreachMobilePixel"
            };

            foreach (string folder in folders)
            {
                if (!AssetDatabase.IsValidFolder(folder))
                {
                    Directory.CreateDirectory(folder);
                }
            }
        }

        private static void ConfigurePlayerSettings()
        {
            NamedBuildTarget webgl = NamedBuildTarget.WebGL;

            PlayerSettings.companyName = "Breach";
            PlayerSettings.productName = "Breach";
            PlayerSettings.bundleVersion = "0.1.0";
            PlayerSettings.SetApplicationIdentifier(webgl, "com.breach.game");
            PlayerSettings.SetApiCompatibilityLevel(webgl, ApiCompatibilityLevel.NET_Standard);
            PlayerSettings.SetManagedStrippingLevel(webgl, ManagedStrippingLevel.Medium);
            PlayerSettings.SetIl2CppCodeGeneration(webgl, Il2CppCodeGeneration.OptimizeSize);

            PlayerSettings.defaultWebScreenWidth = WebCanvasWidth;
            PlayerSettings.defaultWebScreenHeight = WebCanvasHeight;
            PlayerSettings.defaultInterfaceOrientation = UIOrientation.Portrait;
            PlayerSettings.allowedAutorotateToLandscapeLeft = false;
            PlayerSettings.allowedAutorotateToLandscapeRight = false;
            PlayerSettings.allowedAutorotateToPortrait = true;
            PlayerSettings.allowedAutorotateToPortraitUpsideDown = false;
            PlayerSettings.runInBackground = true;

            PlayerSettings.WebGL.template = WebTemplate;
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Brotli;
            PlayerSettings.WebGL.dataCaching = true;
            PlayerSettings.WebGL.debugSymbolMode = WebGLDebugSymbolMode.Off;
            PlayerSettings.WebGL.decompressionFallback = true;
            PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.ExplicitlyThrownExceptionsOnly;
            PlayerSettings.WebGL.initialMemorySize = 128;
            PlayerSettings.WebGL.maximumMemorySize = 1024;
            PlayerSettings.WebGL.memoryGrowthMode = WebGLMemoryGrowthMode.Geometric;
            PlayerSettings.WebGL.nameFilesAsHashes = true;
            PlayerSettings.WebGL.powerPreference = WebGLPowerPreference.HighPerformance;
            PlayerSettings.WebGL.showDiagnostics = false;
            PlayerSettings.WebGL.threadsSupport = false;
            PlayerSettings.WebGL.enableWebGPU = false;
        }

        private static void ConfigureQualitySettings()
        {
            QualitySettings.SetQualityLevel(Mathf.Min(2, QualitySettings.count - 1), false);
            QualitySettings.vSyncCount = 0;
            QualitySettings.antiAliasing = 0;
            QualitySettings.shadows = ShadowQuality.Disable;
            QualitySettings.shadowDistance = 0f;
            QualitySettings.realtimeReflectionProbes = false;
            QualitySettings.softParticles = false;
            QualitySettings.pixelLightCount = 0;
        }

        private static void ConfigurePhysicsSettings()
        {
            Physics2D.gravity = Vector2.zero;
            Time.fixedDeltaTime = 1f / BreachWebRuntime.TargetFrameRate;
            Time.maximumDeltaTime = 1f / 3f;
        }

        private static void ConfigureScene()
        {
            UnityEngine.SceneManagement.Scene scene = EditorSceneManager.GetActiveScene();
            if (!scene.IsValid())
            {
                scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            }

            Camera camera = Camera.main;
            if (camera == null)
            {
                GameObject cameraObject = GameObject.Find("Main Camera") ?? new GameObject("Main Camera");
                cameraObject.tag = "MainCamera";
                camera = cameraObject.GetComponent<Camera>();
                if (camera == null)
                {
                    camera = cameraObject.AddComponent<Camera>();
                }
            }

            camera.name = "Main Camera";
            camera.orthographic = true;
            camera.orthographicSize = BreachWebRuntime.ReferenceHeight / (BreachWebRuntime.AssetsPixelsPerUnit * 2f);
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color32(10, 10, 10, 255);
            camera.nearClipPlane = -50f;
            camera.farClipPlane = 50f;
            camera.transform.position = new Vector3(0f, 0f, -10f);
            camera.transform.rotation = Quaternion.identity;

            EnsurePixelPerfectCamera(camera);

            EnsureOptionalComponent(camera.gameObject, "Unity.Cinemachine.CinemachineBrain, Unity.Cinemachine");
            EnsureOptionalComponent(camera.gameObject, "Cinemachine.CinemachineBrain, Cinemachine");
            EnsureTilemap("Ground");
            EnsureTilemap("Gameplay");
            EnsureTilemap("Collision");

            if (FindObjectByName("Systems") == null)
            {
                new GameObject("Systems");
            }

            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene, BootstrapScenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(BootstrapScenePath, true) };
        }

        private static void EnsureTilemap(string name)
        {
            GameObject gridObject = FindObjectByName("Grid");
            if (gridObject == null)
            {
                gridObject = new GameObject("Grid");
            }

            Grid grid = gridObject.GetComponent<Grid>();
            if (grid == null)
            {
                grid = gridObject.AddComponent<Grid>();
            }

            grid.cellSize = Vector3.one;

            Transform child = gridObject.transform.Find(name);
            GameObject tilemapObject = child == null ? new GameObject(name) : child.gameObject;
            tilemapObject.transform.SetParent(gridObject.transform, false);

            if (tilemapObject.GetComponent<Tilemap>() == null)
            {
                tilemapObject.AddComponent<Tilemap>();
            }

            TilemapRenderer renderer = tilemapObject.GetComponent<TilemapRenderer>();
            if (renderer == null)
            {
                renderer = tilemapObject.AddComponent<TilemapRenderer>();
            }

            renderer.sortingOrder = name == "Ground" ? 0 : name == "Gameplay" ? 10 : 20;
        }

        private static void EnsurePixelArtPreset()
        {
            const string tempTexturePath = "Assets/_Breach/Settings/PixelPresetSource.png";
            if (!File.Exists(tempTexturePath))
            {
                Texture2D texture = new Texture2D(1, 1, TextureFormat.RGBA32, false);
                texture.SetPixel(0, 0, Color.white);
                texture.Apply();
                File.WriteAllBytes(tempTexturePath, texture.EncodeToPNG());
                UnityEngine.Object.DestroyImmediate(texture);
                AssetDatabase.ImportAsset(tempTexturePath);
            }

            TextureImporter importer = AssetImporter.GetAtPath(tempTexturePath) as TextureImporter;
            if (importer == null)
            {
                Debug.LogWarning("Could not create pixel art texture importer preset.");
                return;
            }

            importer.textureType = TextureImporterType.Sprite;
            importer.spriteImportMode = SpriteImportMode.Single;
            importer.spritePixelsPerUnit = BreachWebRuntime.AssetsPixelsPerUnit;
            importer.filterMode = FilterMode.Point;
            importer.mipmapEnabled = false;
            importer.textureCompression = TextureImporterCompression.Uncompressed;
            importer.wrapMode = TextureWrapMode.Clamp;
            importer.SaveAndReimport();

            Preset preset = AssetDatabase.LoadAssetAtPath<Preset>(PixelArtPresetPath);
            if (preset == null)
            {
                preset = new Preset(importer);
                AssetDatabase.CreateAsset(preset, PixelArtPresetPath);
            }
            else
            {
                preset.UpdateProperties(importer);
            }

            PresetType presetType = preset.GetPresetType();
            DefaultPreset[] defaults = Preset.GetDefaultPresetsForType(presetType)
                .Where(defaultPreset => defaultPreset.preset != null && AssetDatabase.GetAssetPath(defaultPreset.preset) != PixelArtPresetPath)
                .Concat(new[] { new DefaultPreset("t:TextureImporter", preset, true) })
                .ToArray();

            Preset.SetDefaultPresetsForType(presetType, defaults);
            AssetDatabase.DeleteAsset(tempTexturePath);
        }

        private static GameObject FindObjectByName(string name)
        {
            return UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsInactive.Include, FindObjectsSortMode.None)
                .FirstOrDefault(go => go.name == name);
        }

        private static void EnsureOptionalComponent(GameObject target, string typeName)
        {
            Type type = Type.GetType(typeName);
            if (type != null && target.GetComponent(type) == null)
            {
                target.AddComponent(type);
            }
        }

        private static void EnsurePixelPerfectCamera(Camera camera)
        {
            Type pixelPerfectType = Type.GetType("UnityEngine.U2D.PixelPerfectCamera, Unity.2D.PixelPerfect");
            if (pixelPerfectType == null)
            {
                Debug.LogWarning("Pixel Perfect Camera package is not loaded; camera pixel setup was skipped.");
                return;
            }

            Component pixelPerfect = camera.GetComponent(pixelPerfectType);
            if (pixelPerfect == null)
            {
                pixelPerfect = camera.gameObject.AddComponent(pixelPerfectType);
            }
            SetComponentProperty(pixelPerfect, "assetsPPU", BreachWebRuntime.AssetsPixelsPerUnit);
            SetComponentProperty(pixelPerfect, "refResolutionX", BreachWebRuntime.ReferenceWidth);
            SetComponentProperty(pixelPerfect, "refResolutionY", BreachWebRuntime.ReferenceHeight);
            SetComponentProperty(pixelPerfect, "upscaleRT", true);
            SetComponentProperty(pixelPerfect, "pixelSnapping", true);
            SetComponentProperty(pixelPerfect, "cropFrameX", false);
            SetComponentProperty(pixelPerfect, "cropFrameY", false);
            SetComponentProperty(pixelPerfect, "stretchFill", false);
        }

        private static void SetComponentProperty(Component component, string propertyName, object value)
        {
            Type type = component.GetType();
            System.Reflection.PropertyInfo property = type.GetProperty(propertyName);
            if (property != null && property.CanWrite)
            {
                property.SetValue(component, value, null);
            }
        }

        private static void EnsurePortraitGameViewSize()
        {
            try
            {
                Type editorAssemblyMarker = typeof(UnityEditor.Editor);
                System.Reflection.Assembly editorAssembly = editorAssemblyMarker.Assembly;
                Type gameViewSizesType = editorAssembly.GetType("UnityEditor.GameViewSizes");
                Type gameViewSizeType = editorAssembly.GetType("UnityEditor.GameViewSize");
                Type gameViewSizeEnumType = editorAssembly.GetType("UnityEditor.GameViewSizeType");
                Type gameViewType = editorAssembly.GetType("UnityEditor.GameView");

                Type singletonType = typeof(ScriptableSingleton<>).MakeGenericType(gameViewSizesType);
                object gameViewSizes = singletonType.GetProperty("instance").GetValue(null, null);
                object group = gameViewSizesType.GetProperty("currentGroup").GetValue(gameViewSizes, null);
                string label = $"Breach Portrait {WebCanvasWidth}x{WebCanvasHeight}";

                System.Reflection.MethodInfo getDisplayTexts = group.GetType().GetMethod("GetDisplayTexts");
                System.Reflection.MethodInfo addCustomSize = group.GetType().GetMethod("AddCustomSize");
                System.Reflection.MethodInfo getTotalCount = group.GetType().GetMethod("GetTotalCount");
                string[] displayTexts = (string[])getDisplayTexts.Invoke(group, null);

                if (!displayTexts.Any(text => text.Contains(label)))
                {
                    object fixedResolution = Enum.Parse(gameViewSizeEnumType, "FixedResolution");
                    object gameViewSize = Activator.CreateInstance(gameViewSizeType, fixedResolution, WebCanvasWidth, WebCanvasHeight, label);
                    addCustomSize.Invoke(group, new[] { gameViewSize });
                    displayTexts = (string[])getDisplayTexts.Invoke(group, null);
                }

                int selectedIndex = Array.FindIndex(displayTexts, text => text.Contains(label));
                if (selectedIndex >= 0)
                {
                    EditorWindow gameView = EditorWindow.GetWindow(gameViewType);
                    gameViewType.GetProperty("selectedSizeIndex").SetValue(gameView, selectedIndex, null);
                    gameView.Repaint();
                    Debug.Log($"Selected Game view size: {label}.");
                }
                else
                {
                    Debug.Log($"Added portrait Game view size; total sizes now {getTotalCount.Invoke(group, null)}.");
                }
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"Could not set the editor Game view size automatically: {exception.Message}");
            }
        }
    }
}
