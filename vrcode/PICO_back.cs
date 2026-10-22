using System;
using System.Collections;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Text;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using UnityEngine.XR.Interaction.Toolkit;
using Unity.XR.PXR;

/*
PICO_v1.cs — P300 实验主控制器
================================================================

【端口约定】
  • TCP 端口: 12345（Python EEG 平台 ← VR marker 推送）

【数据发送格式】
  • "X1"-"X8" → Target 刺激（目标物体 1-8）
  • "G1"-"G8" → Standard 刺激（非目标物体 1-8）
  • "Y"       → 被试按键反应
  • "GAZE:x,y" → 眼动注视点（归一化坐标）
  • "GAZE_ID:n" → 注视目标 ID（1-8）

【如何配置】
  • 运行时通过 UI InputField 设置 IP 地址和端口
*/

public class PICO_v1 : MonoBehaviour
{
    // ── 眼动 & 按键引用 ──────────────────────────────────────────────
    private EyeTracker eyeTracker;
    private ButtonRecord buttonRecord;

    // ── 实验参数 ─────────────────────────────────────────────────────
    private float breakTime = 0.2f;       // 刺激间隔（默认 200ms）
    private float continueTime = 0.5f;    // 刺激持续时间（默认 500ms）
    private float CueTime = 2f;           // 提示持续时间（默认 2s）
    private float restTime = 10f;         // block 间休息时间（默认 10s）

    public InputField inputFieldContinueTime;
    public InputField inputFieldBreakTime;
    public InputField inputFieldCueTime;
    public InputField inputFieldRestTime;

    // block 参数
    private int totalRuns = 10;           // 每个 block 包含 10 个 run
    private int[] blockCue = new int[] { 1, 2, 3, 4, 5, 6, 7, 8, 1, 2 };
    public InputField inputFieldCue;
    private int totalBlocks;

    // ── 颜色和大小设置 ──────────────────────────────────────────────
    public Material Green;
    public Material Red;
    private float objectScale = 0.08f;
    public Slider scaleSlider;

    // 8 个目标物体
    private List<GameObject> allObject = new List<GameObject>();
    public GameObject G1, G2, G3, G4, G5, G6, G7, G8;

    // 8 个音频提示
    private List<AudioSource> allAudios = new List<AudioSource>();
    public AudioSource A1, A2, A3, A4, A5, A6, A7, A8;

    // 动作提示的身体部位
    public GameObject Person;
    public GameObject Head;
    private Vector3 PersonOrigin;
    private Vector3 HeadOrigin;

    // ── UI ───────────────────────────────────────────────────────────
    public GameObject GUI;
    public GameObject endUI;
    public GameObject UI5, UI4, UI3, UI2, UI1;

    // 手柄
    public XRInteractorLineVisual rightLineVisual;
    public XRInteractorLineVisual leftLineVisual;
    public InputActionReference pressA;
    public InputActionReference pressX;

    // ── 模式 ─────────────────────────────────────────────────────────
    private int mode = 0;   // 0=空闲, 1=视觉, 2=听觉, 3=动作

    // ── TCP 通信（硬编码 IP + 端口）────────────────────────────────
    private TcpClient tcpClient;
    private NetworkStream stream;
    private bool isConnected = false;

    // ★ 硬编码 IP 和端口（运行时可通过 UI 修改）
    private string IPAddress = "192.168.1.101";   // Python EEG 平台所在 PC 的 IP
    private int Port = 12345;                    // TCP 端口
    public InputField inputFieldIP;               // UI 上可修改 IP
    public InputField inputFieldPort;             // UI 上可修改端口

    // ── Unity 生命周期 ───────────────────────────────────────────────

    void Start()
    {
        // 眼动引用
        GameObject mainCamera = GameObject.FindWithTag("MainCamera");
        if (mainCamera != null)
            eyeTracker = mainCamera.GetComponent<EyeTracker>();

        // 按键引用
        GameObject shiYan = GameObject.FindWithTag("ShiYan");
        if (shiYan != null)
            buttonRecord = shiYan.GetComponent<ButtonRecord>();

        // 物体列表
        allObject.AddRange(new[] { G1, G2, G3, G4, G5, G6, G7, G8 });
        allAudios.AddRange(new[] { A1, A2, A3, A4, A5, A6, A7, A8 });

        // 保存初始位置
        PersonOrigin = Person.transform.eulerAngles;
        HeadOrigin = Head.transform.eulerAngles;
    }

    void Update()
    {
        // A/X 手柄按键触发实验
        if (pressA.action.WasPerformedThisFrame() || pressX.action.WasPerformedThisFrame())
        {
            switch (mode)
            {
                case 1: ExecuteMode1(); break;
                case 2: ExecuteMode2(); break;
                case 3: ExecuteMode3(); break;
            }
        }

        // 监听 PC 端发来的 TCP 指令（如"START"、"STOP"等）
        if (isConnected && stream != null && stream.DataAvailable)
        {
            byte[] buf = new byte[256];
            int n = stream.Read(buf, 0, buf.Length);
            if (n > 0)
            {
                string cmd = Encoding.UTF8.GetString(buf, 0, n).Trim();
                Debug.Log($"[PICO] 收到 PC 指令: {cmd}");
                HandleTCPCmd(cmd);
            }
        }
    }

    /// <summary>
    /// 处理 PC 端通过 TCP 发来的控制指令。
    /// </summary>
    void HandleTCPCmd(string cmd)
    {
        if (cmd.StartsWith("MODE:"))
        {
            if (int.TryParse(cmd.Substring(5), out int m) && m >= 1 && m <= 3)
            {
                mode = m;
                Debug.Log($"[PICO] 模式切换为 {m}");
            }
        }
    }

    // ── 实验协程 ─────────────────────────────────────────────────────

    void ExecuteMode1() { mode = 0; StartCoroutine(PPP1()); }
    void ExecuteMode2() { mode = 0; StartCoroutine(PPP2()); }
    void ExecuteMode3() { mode = 0; StartCoroutine(PPP3()); }

    // mode1: 视觉提示
    IEnumerator PPP1()
    {
        // 与上位机连接
        LabelConnect(IPAddress, Port);

        yield return StartCoroutine(DaoJiShi());
        totalBlocks = blockCue.Length;

        for (int blockCount = 0; blockCount < totalBlocks; blockCount++)
        {
            foreach (GameObject obj in allObject) obj.SetActive(true);
            int objectIndex = blockCue[blockCount] - 1;
            allObject[objectIndex].GetComponent<Renderer>().material = Red;

            yield return new WaitForSeconds(CueTime);
            foreach (GameObject obj in allObject) obj.SetActive(false);

            List<int> indices = new List<int>();
            for (int i = 0; i < allObject.Count; i++) indices.Add(i);

            for (int runCount = 0; runCount < totalRuns; runCount++)
            {
                // Fisher-Yates 乱序
                for (int i = 0; i < indices.Count; i++)
                {
                    int ri = UnityEngine.Random.Range(0, indices.Count);
                    (indices[i], indices[ri]) = (indices[ri], indices[i]);
                }

                for (int i = 0; i < indices.Count; i++)
                {
                    eyeTracker.targetIndicesString = "";
                    buttonRecord.buttonRecordString = "";

                    int random = indices[i];
                    GameObject thisOb = allObject[random];

                    SendData(blockCue[blockCount] == random + 1
                        ? $"X{random + 1}"
                        : $"G{random + 1}");

                    thisOb.GetComponent<Renderer>().material = Green;
                    thisOb.SetActive(true);
                    yield return new WaitForSeconds(continueTime);
                    thisOb.SetActive(false);
                    yield return new WaitForSeconds(breakTime);

                    SendData(eyeTracker.targetIndicesString + "\n");
                    SendData(buttonRecord.buttonRecordString + "\n");
                }
            }

            yield return new WaitForSeconds(restTime);
        }

        EndExperiment();
    }

    // mode2: 听觉提示
    IEnumerator PPP2()
    {
        LabelConnect(IPAddress, Port);

        yield return StartCoroutine(DaoJiShi());
        totalBlocks = blockCue.Length;

        for (int blockCount = 0; blockCount < totalBlocks; blockCount++)
        {
            foreach (GameObject obj in allObject) obj.SetActive(true);
            allAudios[blockCue[blockCount] - 1].Play();
            yield return new WaitForSeconds(CueTime);
            foreach (GameObject obj in allObject) obj.SetActive(false);

            List<int> indices = new List<int>();
            for (int i = 0; i < allObject.Count; i++) indices.Add(i);

            for (int runCount = 0; runCount < totalRuns; runCount++)
            {
                for (int i = 0; i < indices.Count; i++)
                {
                    int ri = UnityEngine.Random.Range(0, indices.Count);
                    (indices[i], indices[ri]) = (indices[ri], indices[i]);
                }

                for (int i = 0; i < indices.Count; i++)
                {
                    eyeTracker.targetIndicesString = "";
                    buttonRecord.buttonRecordString = "";

                    int random = indices[i];
                    GameObject thisOb = allObject[random];

                    SendData(blockCue[blockCount] == random + 1
                        ? $"X{random + 1}"
                        : $"G{random + 1}");

                    thisOb.GetComponent<Renderer>().material = Green;
                    thisOb.SetActive(true);
                    yield return new WaitForSeconds(continueTime);
                    thisOb.SetActive(false);
                    yield return new WaitForSeconds(breakTime);

                    SendData(eyeTracker.targetIndicesString + "\n");
                    SendData(buttonRecord.buttonRecordString + "\n");
                }
            }

            yield return new WaitForSeconds(restTime);
        }

        EndExperiment();
    }

    // mode3: 动作提示
    IEnumerator PPP3()
    {
        LabelConnect(IPAddress, Port);

        yield return StartCoroutine(DaoJiShi());
        totalBlocks = blockCue.Length;

        for (int blockCount = 0; blockCount < totalBlocks; blockCount++)
        {
            foreach (GameObject obj in allObject) obj.SetActive(true);
            StartCoroutine(MotionCue(blockCue[blockCount]));
            yield return new WaitForSeconds(CueTime);
            foreach (GameObject obj in allObject) obj.SetActive(false);

            List<int> indices = new List<int>();
            for (int i = 0; i < allObject.Count; i++) indices.Add(i);

            for (int runCount = 0; runCount < totalRuns; runCount++)
            {
                for (int i = 0; i < indices.Count; i++)
                {
                    int ri = UnityEngine.Random.Range(0, indices.Count);
                    (indices[i], indices[ri]) = (indices[ri], indices[i]);
                }

                for (int i = 0; i < indices.Count; i++)
                {
                    eyeTracker.targetIndicesString = "";
                    buttonRecord.buttonRecordString = "";

                    int random = indices[i];
                    GameObject thisOb = allObject[random];

                    SendData(blockCue[blockCount] == random + 1
                        ? $"X{random + 1}"
                        : $"G{random + 1}");

                    thisOb.GetComponent<Renderer>().material = Green;
                    thisOb.SetActive(true);
                    yield return new WaitForSeconds(continueTime);
                    thisOb.SetActive(false);
                    yield return new WaitForSeconds(breakTime);

                    SendData(eyeTracker.targetIndicesString + "\n");
                    SendData(buttonRecord.buttonRecordString + "\n");
                }
            }

            Person.transform.eulerAngles = PersonOrigin;
            Head.transform.eulerAngles = HeadOrigin;
            yield return new WaitForSeconds(restTime);
        }

        EndExperiment();
    }

    void EndExperiment()
    {
        endUI.SetActive(true);
        setLineActive(true);
        StartCoroutine(ResetAfterDelay());
    }

    IEnumerator ResetAfterDelay()
    {
        yield return new WaitForSeconds(2f);
        endUI.SetActive(false);
        GUI.SetActive(true);
    }

    // ── 动作提示 ─────────────────────────────────────────────────────

    IEnumerator MotionCue(int target)
    {
        Vector3 newPersonRotation = Person.transform.eulerAngles;
        Vector3 newHeadRotation = Head.transform.eulerAngles;

        switch (target)
        {
            case 2:
                newHeadRotation.x += -20f;
                newHeadRotation.y += 20f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;
            case 4:
                newHeadRotation.x = -35f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;
            case 6:
                newHeadRotation.x += -30f;
                newHeadRotation.y += -30f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;
            case 8:
                newHeadRotation.x = 20f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;
        }
    }

    // ── TCP 数据发送 ─────────────────────────────────────────────────

    void SendData(string message)
    {
        if (stream == null || !isConnected) return;
        try
        {
            byte[] data = Encoding.UTF8.GetBytes(message);
            stream.Write(data, 0, data.Length);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[PICO] TCP 发送失败: {e.Message}");
            isConnected = false;
        }
    }

    // ── 与 Python EEG 平台连接 ───────────────────────────────────────

    void LabelConnect(string IPAddress, int Port)
    {
        try
        {
            tcpClient = new TcpClient(IPAddress, Port);
            stream = tcpClient.GetStream();
            isConnected = true;
            Debug.Log($"[PICO] 已连接到 {IPAddress}:{Port}");
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[PICO] 连接失败: {e.Message}");
            isConnected = false;
        }
    }

    // ── 倒计时 ──────────────────────────────────────────────────────

    IEnumerator DaoJiShi()
    {
        UI5.SetActive(true); yield return new WaitForSeconds(1f); UI5.SetActive(false);
        UI4.SetActive(true); yield return new WaitForSeconds(1f); UI4.SetActive(false);
        UI3.SetActive(true); yield return new WaitForSeconds(1f); UI3.SetActive(false);
        UI2.SetActive(true); yield return new WaitForSeconds(1f); UI2.SetActive(false);
        UI1.SetActive(true); yield return new WaitForSeconds(1f); UI1.SetActive(false);
    }

    // ── UI 回调 ────────────────────────────────────────────────────

    public void mode1ButtonClick()
    {
        mode = 1;
        GUI.SetActive(false);
        setLineActive(false);
        foreach (GameObject obj in allObject) obj.SetActive(false);
    }

    public void mode2ButtonClick()
    {
        mode = 2;
        GUI.SetActive(false);
        setLineActive(false);
        foreach (GameObject obj in allObject) obj.SetActive(false);
    }

    public void mode3ButtonClick()
    {
        mode = 3;
        GUI.SetActive(false);
        setLineActive(false);
        foreach (GameObject obj in allObject) obj.SetActive(false);
    }

    public void continueTimeInputEnd()
    {
        if (float.TryParse(inputFieldContinueTime.text, out float v))
            continueTime = v * 0.001f;
    }

    public void breakTimeInputEnd()
    {
        if (float.TryParse(inputFieldBreakTime.text, out float v))
            breakTime = v * 0.001f;
    }

    public void CueTimeInputEnd()
    {
        if (float.TryParse(inputFieldCueTime.text, out float v))
            CueTime = v;
    }

    public void restTimeInputEnd()
    {
        if (float.TryParse(inputFieldRestTime.text, out float v))
            restTime = v;
    }

    public void IPInputEnd()
    {
        IPAddress = inputFieldIP.text;
    }

    public void PortInputEnd()
    {
        if (int.TryParse(inputFieldPort.text, out int p) && p > 0 && p < 65536)
            Port = p;
    }

    public void CueInputEnd()
    {
        string txt = inputFieldCue.text;
        blockCue = new int[txt.Length];
        for (int i = 0; i < txt.Length; i++)
        {
            if (int.TryParse(txt[i].ToString(), out int cue))
                blockCue[i] = cue;
        }
    }

    public void setScaleSlider(Single value)
    {
        objectScale = scaleSlider.value;
        foreach (GameObject obj in allObject)
            obj.transform.localScale = new Vector3(objectScale, 1f, objectScale);
    }

    void setLineActive(bool a)
    {
        if (rightLineVisual != null) rightLineVisual.enabled = a;
        if (leftLineVisual != null) leftLineVisual.enabled = a;
    }
}
