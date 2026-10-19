// MIT License. Place this file in Assets/Editor after installing official Unity glTFast.
#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;
using UnityEngine;

public static class MeshToPlayLODGroup
{
    [MenuItem("Tools/Mesh to Play/Build LODGroup from imported GLBs")]
    private static void Build()
    {
        string folder = EditorUtility.OpenFolderPanel("Select an Assets folder containing LOD0.glb, LOD1.glb, LOD2.glb", Application.dataPath, "");
        if (string.IsNullOrEmpty(folder)) return;
        string absolute = Path.GetFullPath(folder).Replace('\\', '/').TrimEnd('/');
        string assets = Path.GetFullPath(Application.dataPath).Replace('\\', '/').TrimEnd('/');
        if (!(absolute + "/").StartsWith(assets + "/", StringComparison.Ordinal))
        {
            EditorUtility.DisplayDialog("Mesh to Play", "Copy the three output GLBs into this project's Assets folder first. Install com.unity.cloud.gltfast through Package Manager so its Editor importer handles .glb files.", "OK");
            return;
        }
        string relative = "Assets" + absolute.Substring(assets.Length);
        var prefabs = new GameObject[3];
        for (int i = 0; i < 3; i++)
        {
            string path = relative + "/LOD" + i + ".glb";
            if (!File.Exists(Path.Combine(absolute, "LOD" + i + ".glb")))
            {
                EditorUtility.DisplayDialog("Mesh to Play", "Missing " + path, "OK");
                return;
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            prefabs[i] = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (prefabs[i] == null)
            {
                EditorUtility.DisplayDialog("Mesh to Play", "No imported model at " + path + ". Install the official com.unity.cloud.gltfast package and resolve importer or shader errors in the Console.", "OK");
                return;
            }
        }
        GameObject root = new GameObject("Mesh to Play LODs");
        Undo.RegisterCreatedObjectUndo(root, "Build Mesh to Play LODGroup");
        try
        {
            var levels = new LOD[3];
            float[] transitions = { 0.6f, 0.25f, 0.08f };
            for (int i = 0; i < 3; i++)
            {
                GameObject child = (GameObject)PrefabUtility.InstantiatePrefab(prefabs[i], root.transform);
                child.name = "LOD" + i;
                child.transform.localPosition = Vector3.zero;
                child.transform.localRotation = Quaternion.identity;
                child.transform.localScale = Vector3.one;
                Renderer[] renderers = child.GetComponentsInChildren<Renderer>(true);
                if (renderers.Length == 0) throw new InvalidOperationException("Imported LOD" + i + " has no renderers.");
                levels[i] = new LOD(transitions[i], renderers);
            }
            LODGroup group = root.AddComponent<LODGroup>();
            group.SetLODs(levels);
            group.RecalculateBounds();
            Selection.activeGameObject = root;
            EditorGUIUtility.PingObject(root);
        }
        catch (Exception error)
        {
            Undo.DestroyObjectImmediate(root);
            Debug.LogException(error);
        }
    }
}
#endif
