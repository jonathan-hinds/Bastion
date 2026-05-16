using UnityEngine;

namespace Breach.Foundation
{
    public static class BreachWebRuntime
    {
        public const int ReferenceWidth = 180;
        public const int ReferenceHeight = 320;
        public const int AssetsPixelsPerUnit = 16;
        public const int TargetFrameRate = 60;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void ApplyRuntimeDefaults()
        {
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = TargetFrameRate;
            Screen.sleepTimeout = SleepTimeout.NeverSleep;

#if UNITY_WEBGL && !UNITY_EDITOR
            Application.runInBackground = true;
#endif
        }
    }
}
