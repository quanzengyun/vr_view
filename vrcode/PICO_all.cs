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

public class PICO : MonoBehaviour
{
    private EyeTracker eyeTracker;
    private ButtonRecord buttonRecord;

    // 实验参数
    private float breakTime = 0.2f;  // 预定义持续时间（默认为200毫秒）
    private float continueTime = 0.5f;  // 预定义间隔时间（默认为500毫秒）
    private float CueTime = 2f; // 预定义视觉提示持续时间（默认为2秒）
    private float restTime = 10f;  // 预定义block间休息时间（默认为10秒）
    public InputField inputFieldContinueTime;  // InputField输入设置持续时间
    public InputField inputFieldBreakTime;  // InputField输入设置间隔时间
    public InputField inputFieldCueTime;  // InputField输入设置视觉提示持续时间
    public InputField inputFieldRestTime;  // InputField输入设置视觉提示持续时间
    // block参数
    private int totalRuns = 10;  // 每个block包含10个run
    private int[] blockCue = new int[] { 1, 2, 3, 4, 5, 6, 7, 8, 1, 2 };  // 预定义每个block的目标（提示）物体
    public InputField inputFieldCue;  // InputField输入设置每个block的目标（提示）物体
    private int totalBlocks;  // block总数(循环次数)


    // 颜色和大小设置
    public Material Green;  // 绿色
    public Material Red;  // 红色
    private float objectScale = 0.08f;  // 预定义刺激大小为0.08
    public Slider scaleSlider;  // slider设置刺激大小

    // 定义8个目标物体
    private List<GameObject> allObject = new List<GameObject>();  // 列表，储存所有目标物体
    public GameObject G1;
    public GameObject G2;
    public GameObject G3;
    public GameObject G4;
    public GameObject G5;
    public GameObject G6;
    public GameObject G7;
    public GameObject G8;
    // 定义8个音频提示
    private List<AudioSource> allAudios = new List<AudioSource>();  // 列表，储存所有提示音频
    public AudioSource A1;
    public AudioSource A2;
    public AudioSource A3;
    public AudioSource A4;
    public AudioSource A5;
    public AudioSource A6;
    public AudioSource A7;
    public AudioSource A8;
    //// 定义动作提示的8个红线
    //private List<GameObject> allLines = new List<GameObject>();  // 列表，储存所有红色提示线
    //public GameObject L1;
    //public GameObject L2;
    //public GameObject L3;
    //public GameObject L4;
    //public GameObject L5;
    //public GameObject L6;
    //public GameObject L7;
    //public GameObject L8;

    // 定义动作提示的身体部位
    //public GameObject LArm;  // 左手臂
    //public GameObject RArm;  // 右手臂
    public GameObject Person;  // 人物整体
    public GameObject Head;  // 人物头部
    // 保存各部位的初始位置
    //private Vector3 LArmOrigin;  // 左手臂的初始位置
    //private Vector3 RArmOrigin;  // 右手臂的初始位置
    private Vector3 PersonOrigin;  // 人物的初始位置
    private Vector3 HeadOrigin;  // 头部的初始位置

    // 定义UI物体
    public GameObject GUI; // 参数设置UI（实验前）
    public GameObject endUI; // 结束提示UI（实验后）
    // 倒计时UI
    public GameObject UI5;
    public GameObject UI4;
    public GameObject UI3;
    public GameObject UI2;
    public GameObject UI1;

    // 定义手柄按键
    public XRInteractorLineVisual rightLineVisual;  // 右手射线
    public XRInteractorLineVisual leftLineVisual;  // 左手射线
    public InputActionReference pressA;  // 右手柄A键
    public InputActionReference pressX;  // 左手柄X键

    // 设置初始运行模式mode=0
    private int mode = 0;

    // tcp-ip 通信
    private TcpClient tcpClient;
    private NetworkStream stream;
    // PC 端 IP 从 StreamingAssets/vr_config.json 读，由 PC 端 vr_config.py 维护
    private string IPAddress = VRConfigLoader.DEFAULT_PC_IP;
    private int Port = VRConfigLoader.DEFAULT_TRIG_PORT;
    public InputField inputFieldIP;  // InputField输入设置IP地址

    // ===== 视频流（独立 TCP 连接，端口 12346，避免和 trigger 串）=====
    private TcpClient videoTcp;
    private NetworkStream videoStream;
    private bool videoLoopRunning = false;
    private int VideoPort = VRConfigLoader.DEFAULT_VIDEO_PORT;   // 视频端口（从配置读）
    private int VideoFps = 30;              // 视频帧率
    private int VideoWidth = 640;           // 缩到 640（省带宽）
    private int VideoHeight = 360;          // 缩到 360
    private int VideoJpegQuality = 50;      // JPEG 质量 0-100
    private RenderTexture videoRT;          // 离屏渲染纹理
    private Texture2D videoTex;             // 用来 EncodeToJPG
    private int videoFrameSeq = 0;          // 帧序号（用于PC端丢帧）
    private int lastAckSeq = -1;            // PC 端最新确认帧
    // 帧协议：[4B 魔数 MAGIC=0x56525646 'VRVF'] [4B seq] [4B len] [len B JPEG]
    private static readonly byte[] VRVF_MAGIC = new byte[] { (byte)'V', (byte)'R', (byte)'V', (byte)'F' };

    // Start is called before the first frame update
    void Start()
    {
        // ★ 启动时读 PC 端 vr_config.py 维护的配置文件，覆盖硬编码 IP
        try
        {
            var cfg = VRConfigLoader.Load();
            IPAddress = cfg.ip;
            Port = cfg.trigPort;
            VideoPort = cfg.videoPort;
            Debug.Log($"[PICO] 启动配置：PC={IPAddress} trigger={Port} video={VideoPort}");
        }
        catch (System.Exception e)
        {
            Debug.LogError("[PICO] 读 vr_config.json 失败，使用硬编码默认值: " + e.Message);
        }

        // 获取挂载在MainCamera上的EyeTracker脚本(眼动脚本)
        GameObject mainCamera = GameObject.FindWithTag("MainCamera");
        if (mainCamera != null)
        {
            eyeTracker = mainCamera.GetComponent<EyeTracker>();
        }
        // 获取挂载在ShiYan上的ButtonRecord脚本(按键记录脚本)
        GameObject shiYan = GameObject.FindWithTag("ShiYan");
        if (shiYan != null)
        {
            buttonRecord = shiYan.GetComponent<ButtonRecord>();
        }

        // 将所有刺激物体存入列表中
        allObject.Add(G1);
        allObject.Add(G2);
        allObject.Add(G3);
        allObject.Add(G4);
        allObject.Add(G5);
        allObject.Add(G6);
        allObject.Add(G7);
        allObject.Add(G8);
        // 将所有音频存入列表中
        allAudios.Add(A1);
        allAudios.Add(A2);
        allAudios.Add(A3);
        allAudios.Add(A4);
        allAudios.Add(A5);
        allAudios.Add(A6);
        allAudios.Add(A7);
        allAudios.Add(A8);
        //// 将所有红色提示线存入列表中
        //allLines.Add(L1);
        //allLines.Add(L2);
        //allLines.Add(L3);
        //allLines.Add(L4);
        //allLines.Add(L5);
        //allLines.Add(L6);
        //allLines.Add(L7);
        //allLines.Add(L8);

        // 获取身体部位的初始位置(欧拉角)
        //LArmOrigin = LArm.transform.eulerAngles;
        //RArmOrigin = RArm.transform.eulerAngles;
        PersonOrigin = Person.transform.eulerAngles;
        HeadOrigin = Head.transform.eulerAngles;

        // 启动视频流（独立协程，独立 TCP，端口 12346）
        StartVideoStream();
    }

    // mode1ִ执行协程
    IEnumerator PPP1()
    {
        // 与上位机连接
        yield return StartCoroutine(LabelConnectAsync(IPAddress, Port));

        // 倒计时
        StartCoroutine(DaoJiShi());
        yield return new WaitForSeconds(5.2f);

        // 呈现实验范式
        totalBlocks = blockCue.Length;
        for (int blockCount = 0; blockCount < totalBlocks; blockCount++)
        {
            // 激活所有刺激物体
            foreach (GameObject obj in allObject)
            {
                //Renderer re = obj.GetComponent<Renderer>();
                //re.material = Red;
                obj.SetActive(true);
            }
            // 将目标物体设置为红色
            int objectIndex = blockCue[blockCount] - 1;
            Renderer setRenderer = allObject[objectIndex].GetComponent<Renderer>();
            setRenderer.material = Red;  

            // 提示持续时间（默认2秒）
            yield return new WaitForSeconds(CueTime);
            // 失活所有刺激物体
            foreach (GameObject obj in allObject)
            {
                obj.SetActive(false);
            }

            // 创建索引列表
            List<int> indices = new List<int>();
            for (int i = 0; i < allObject.Count; i++)
            {
                indices.Add(i);
            }


            // ִ执行1个block内10个run
            for (int runCount = 0; runCount < totalRuns; runCount++)
            {

                // 每一个run的8个trial都打乱顺序
                for (int i = 0; i < indices.Count; i++)
                {
                    int randomIndex = UnityEngine.Random.Range(0, indices.Count - 1);
                    int temp = indices[i];
                    indices[i] = indices[randomIndex];
                    indices[randomIndex] = temp;
                }

                

                // 按以上顺序显示trial
                for (int i = 0; i < indices.Count; i++)
                {
                    // 每个trial重置眼动信息
                    eyeTracker.targetIndicesString = "";
                    // 每个trial重置按键反应
                    buttonRecord.buttonRecordString = "";

                    int random = indices[i];
                    GameObject thisOb = allObject[random];

                    if (blockCue[blockCount] == (random+1))
                    {
                        SendData("X" + (random + 1));  
                    }
                    else
                    {
                        SendData("G" + (random + 1));
                    }
                    
                    Renderer re = thisOb.GetComponent<Renderer>();
                    re.material = Green;

                    // 刺激呈现
                    thisOb.SetActive(true);
                    yield return new WaitForSeconds(continueTime);  // 刺激持续时间
                    thisOb.SetActive(false);
                    yield return new WaitForSeconds(breakTime);  // 间隔时间

                    // 发送眼动数据
                    SendData(eyeTracker.targetIndicesString);  
                    SendData("\n");

                    // 发送按键反应
                    SendData(buttonRecord.buttonRecordString);
                    SendData("\n");

                }

            }

            // 每一个block结束后休息10秒
            yield return new WaitForSeconds(restTime);

        }

        // 激活结束UI
        endUI.SetActive(true);
        setLineActive(true);
        yield return new WaitForSeconds(2f);

        // 重设实验
        endUI.SetActive(false);
        GUI.SetActive(true);

    }

    // mode2执行协程
    IEnumerator PPP2()
    {
        // 与上位机连接
        yield return StartCoroutine(LabelConnectAsync(IPAddress, Port));

        // 倒计时
        StartCoroutine(DaoJiShi());
        yield return new WaitForSeconds(5.2f);

        // 呈现实验范式
        totalBlocks = blockCue.Length;
        for (int blockCount = 0; blockCount < totalBlocks; blockCount++)
        {
            // 激活所有刺激物体
            foreach (GameObject obj in allObject)
            {
                obj.SetActive(true);
            }
            // 播放音频提示
            int objectIndex = blockCue[blockCount] - 1;
            AudioSource thisAudioCue = allAudios[objectIndex].GetComponent<AudioSource>();
            thisAudioCue.Play();

            // 所有刺激物体持续2秒
            yield return new WaitForSeconds(CueTime);
            // 失活所有刺激物体
            foreach (GameObject obj in allObject)
            {
                obj.SetActive(false);
            }

            // // 创建索引列表
            List<int> indices = new List<int>();
            for (int i = 0; i < allObject.Count; i++)
            {
                indices.Add(i);
            }

            // ִ执行1个block内10个run
            for (int runCount = 0; runCount < totalRuns; runCount++)
            {

                // 每一个run的8个trial都打乱顺序
                for (int i = 0; i < indices.Count; i++)
                {
                    int randomIndex = UnityEngine.Random.Range(0, indices.Count - 1);
                    int temp = indices[i];
                    indices[i] = indices[randomIndex];
                    indices[randomIndex] = temp;
                }

               

                // 按以上顺序显示trial
                for (int i = 0; i < indices.Count; i++)
                {
                    // 每个trial重置眼动信息与按键反应
                    eyeTracker.targetIndicesString = "";
                    // 每个trial重置按键反应
                    buttonRecord.buttonRecordString = "";

                    int random = indices[i];
                    GameObject thisOb = allObject[random];

                    if (blockCue[blockCount] == (random + 1))
                    {
                        SendData("X" + (random + 1));
                    }
                    else
                    {
                        SendData("G" + (random + 1));
                    }

                    Renderer re = thisOb.GetComponent<Renderer>();
                    re.material = Green;

                    // 刺激呈现
                    thisOb.SetActive(true);
                    yield return new WaitForSeconds(continueTime);  // 刺激持续时间
                    thisOb.SetActive(false);
                    yield return new WaitForSeconds(breakTime);  // 间隔时间

                    // 发送眼动数据
                    SendData(eyeTracker.targetIndicesString);  
                    SendData("\n");

                    // 发送按键反应
                    SendData(buttonRecord.buttonRecordString);
                    SendData("\n");

                }


            }

            // 每一个block结束后休息10秒
            yield return new WaitForSeconds(restTime);
        }

        // 激活结束UI
        endUI.SetActive(true);
        setLineActive(true);
        yield return new WaitForSeconds(2f);

        // 重设实验
        endUI.SetActive(false);
        GUI.SetActive(true);

    }

    // mode3ִ执行协程
    IEnumerator PPP3()
    {
        // 与上位机连接
        yield return StartCoroutine(LabelConnectAsync(IPAddress, Port));

        // 倒计时
        StartCoroutine(DaoJiShi());
        yield return new WaitForSeconds(5.2f);

        // 呈现实验范式
        totalBlocks = blockCue.Length;
        for (int blockCount = 0; blockCount < totalBlocks; blockCount++)
        {
            // 激活所有刺激物体
            foreach (GameObject obj in allObject)
            {
                //Renderer re = obj.GetComponent<Renderer>();
                //re.material = Red;
                obj.SetActive(true);
            }

            //// 将目标物体提示线激活
            //int lineIndex = blockCue[blockCount] - 1;
            //allLines[lineIndex].SetActive(true);

            // 此处加入动作提示
            int objectIndex = blockCue[blockCount];
            StartCoroutine(MotionCue(objectIndex));

            // 所有刺激物体持续2秒
            yield return new WaitForSeconds(CueTime);


            // 失活所有刺激物体
            foreach (GameObject obj in allObject)
            {
                //Renderer re = obj.GetComponent<Renderer>();
                //re.material = Red;
                obj.SetActive(false);
            }
            //// 失活提示线
            //allLines[lineIndex].SetActive(false);

            // 创建索引列表
            List<int> indices = new List<int>();
            for (int i = 0; i < allObject.Count; i++)
            {
                indices.Add(i);
            }

            // 执行1个block内10个run
            for (int runCount = 0; runCount < totalRuns; runCount++)
            {

                // 每一个run的8个trial都打乱顺序
                for (int i = 0; i < indices.Count; i++)
                {
                    int randomIndex = UnityEngine.Random.Range(0, indices.Count - 1);
                    int temp = indices[i];
                    indices[i] = indices[randomIndex];
                    indices[randomIndex] = temp;
                }

                

                // 按以上顺序显示trial
                for (int i = 0; i < indices.Count; i++)
                {
                    // 每个trial重置眼动信息与按键反应
                    eyeTracker.targetIndicesString = "";
                    // 每个trial重置按键反应
                    buttonRecord.buttonRecordString = "";

                    int random = indices[i];
                    GameObject thisOb = allObject[random];

                    if (blockCue[blockCount] == (random + 1))
                    {
                        SendData("X" + (random + 1));
                    }
                    else
                    {
                        SendData("G" + (random + 1));
                    }

                    Renderer re = thisOb.GetComponent<Renderer>();
                    re.material = Green;

                    // 刺激呈现
                    thisOb.SetActive(true);
                    yield return new WaitForSeconds(continueTime);  // 刺激持续时间
                    thisOb.SetActive(false);
                    yield return new WaitForSeconds(breakTime);  // 间隔时间

                    // 发送眼动数据
                    SendData(eyeTracker.targetIndicesString);
                    SendData("\n");

                    // 发送按键反应
                    SendData(buttonRecord.buttonRecordString);
                    SendData("\n");
                }

            }

            // 提示动作归零
            Person.transform.eulerAngles = PersonOrigin;  // 人物归位
            Head.transform.eulerAngles = HeadOrigin;  // 头部归位
            //LArm.transform.eulerAngles = LArmOrigin;  // 左臂归位
            //RArm.transform.eulerAngles = RArmOrigin;  // 右臂归位

            // 每一个block结束后休息10秒
            yield return new WaitForSeconds(restTime);
        }

        // 激活结束UI
        endUI.SetActive(true);
        setLineActive(true);
        yield return new WaitForSeconds(2f);

        // 重设实验
        endUI.SetActive(false);
        GUI.SetActive(true);

    }

    // UI--mode1Button执行视觉提示协程
    public void mode1ButtonClick()
    {
        mode = 1;
        // 失活参数设置UI
        GUI.SetActive(false);
        setLineActive(false);
        // 失活所有刺激物体
        foreach (GameObject obj in allObject)
        {
            obj.SetActive(false);
        }
    }
    // UI--mode2Button执行音频提示协程
    public void mode2ButtonClick()
    {
        mode = 2;
        // 失活参数设置UI
        GUI.SetActive(false);
        setLineActive(false);
        // 失活所有刺激物体
        foreach (GameObject obj in allObject)
        {
            obj.SetActive(false);
        }
    }
    // UI--mode3Button执行动作提示协程
    public void mode3ButtonClick()
    {
        mode = 3;
        // 失活参数设置UI
        GUI.SetActive(false);
        setLineActive(false);
        // 失活所有刺激物体
        foreach (GameObject obj in allObject)
        {
            obj.SetActive(false);
        }
    }

    // UI--InputField输入设置持续时间(continueTime)
    public void continueTimeInputEnd()
    {
        // 获取输入的字符串
        string inputFieldContinueTimeText = inputFieldContinueTime.text;

        // 将字符串转换为float类型，并乘以0.001赋值给continueTime
        if (float.TryParse(inputFieldContinueTimeText, out float parsedContinueTime))
        {
            continueTime = parsedContinueTime * 0.001f;
        }
    }
    // UI--InputField输入设置间隔时间(breakTime)
    public void breakTimeInputEnd()
    {
        // 获取输入的字符串
        string inputFieldBreakTimeText = inputFieldBreakTime.text;

        // 将字符串转换为float类型，并乘以0.001赋值给breakTime
        if (float.TryParse(inputFieldBreakTimeText, out float parsedBreakTime))
        {
            breakTime = parsedBreakTime * 0.001f;
        }
    }
    // UI--InputField输入设置提示持续时间(CueTime)
    public void CueTimeInputEnd()
    {
        // 获取输入的字符串
        string inputFieldCueTimeText = inputFieldCueTime.text;

        // 将字符串转换为float类型，赋值给CueTime
        if (float.TryParse(inputFieldCueTimeText, out float parsedCueTime))
        {
            CueTime = parsedCueTime * 1f;
        }
    }
    // UI--InputField输入设置block间休息时间(restTime)
    public void restTimeInputEnd()
    {
        // 获取输入的字符串
        string inputFieldRestTimeText = inputFieldRestTime.text;

        // 将字符串转换为float类型，赋值给CueTime
        if (float.TryParse(inputFieldRestTimeText, out float parsedRestTime))
        {
            restTime = parsedRestTime * 1f;
        }
    }
    // UI--InputField输入设置IP地址ַ
    public void IPInputEnd()
    {
        if (inputFieldIP == null) return;
        string newIP = inputFieldIP.text.Trim();
        if (string.IsNullOrEmpty(newIP))
        {
            Debug.LogWarning("[PICO] IP 输入框为空，仍使用默认值: " + IPAddress);
            return;
        }
        IPAddress = newIP;
        Debug.Log($"[PICO] IP 已更新为: {IPAddress} (Port={Port})");
    }
    // UI--InputField输入设置Cue顺序
    public void CueInputEnd()
    {
        // 获取输入的字符串
        string inputTargetText = inputFieldCue.text;

        // 定义一个新的blockCue数组
        blockCue = new int[inputTargetText.Length];

        // 遍历字符串的每一位，将其转换为int储存进blockCue
        for (int i = 0; i < inputTargetText.Length; i++)
        {
            blockCue[i] = int.Parse(inputTargetText[i].ToString());
           
        }
    }

    // UI--slider设置刺激物体大小(scale)
    public void setScaleSlider(Single value)
    {
        // 获取slider值
        objectScale = scaleSlider.value;

        // 设置每个物体的大小 (scale)
        foreach (GameObject obj in allObject)
        {
            obj.transform.localScale = new Vector3(objectScale, 1f, objectScale);  // 设置(x,y,z)=(0.1,0.2,0.3)
        }
    }

    // 激活/失活手柄射线
    public void setLineActive(bool a)
    {
        if (a)
        {
            rightLineVisual.enabled = true;
            leftLineVisual.enabled = true;
        }
        else 
        {
            rightLineVisual.enabled = false;
            leftLineVisual.enabled = false;
        }
    }

    // 倒计时协程
    IEnumerator DaoJiShi()
    {
        UI5.SetActive(true);  // 5
        yield return new WaitForSeconds(1f);
        UI5.SetActive(false);
        UI4.SetActive(true);  // 4
        yield return new WaitForSeconds(1f);
        UI4.SetActive(false);
        UI3.SetActive(true);  // 3
        yield return new WaitForSeconds(1f);
        UI3.SetActive(false);
        UI2.SetActive(true); // 2
        yield return new WaitForSeconds(1f);
        UI2.SetActive(false);
        UI1.SetActive(true);  // 1
        yield return new WaitForSeconds(1f);
        UI1.SetActive(false);
    }

    // 数据传输（异步写、不阻塞主线程）
    private void SendData(string message)
    {
        // 单独启协程做实际发送，永不阻塞主线程
        StartCoroutine(SendDataAsync(message));
    }

    private System.Collections.IEnumerator SendDataAsync(string message)
    {
        // 快照当前 stream（防止 LabelConnectAsync 在中途清空它）
        NetworkStream localStream = stream;
        if (localStream == null)
        {
            Debug.LogWarning("[PICO] stream 为空，尝试重连后再发: " + message);
            yield return StartCoroutine(LabelConnectAsync(IPAddress, Port));
            localStream = stream;
        }
        if (localStream == null)
        {
            Debug.LogWarning("[PICO] 重连后仍无 stream，跳过: " + message);
            yield break;
        }

        // --- 阶段1：发起异步写（无 yield）---
        byte[] data = null;
        System.Threading.Tasks.Task writeTask = null;
        bool writeStartFailed = false;
        try
        {
            data = Encoding.UTF8.GetBytes(message);
            writeTask = localStream.WriteAsync(data, 0, data.Length);
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("[PICO] 启动写异常: " + e.Message);
            writeStartFailed = true;
        }

        if (writeStartFailed)
        {
            yield return StartCoroutine(LabelConnectAsync(IPAddress, Port));
            yield break;
        }

        // --- 阶段2：轮询等待（带 yield）---
        float t = 0f;
        while (!writeTask.IsCompleted && t < 1f) { t += Time.unscaledDeltaTime; yield return null; }

        if (!writeTask.IsCompleted)
        {
            Debug.LogWarning("[PICO] WriteAsync 超时，丢弃数据: " + message);
            yield break;
        }

        if (writeTask.Exception != null)
        {
            Debug.LogWarning("[PICO] 写异常: " + writeTask.Exception.Message);
            yield return StartCoroutine(LabelConnectAsync(IPAddress, Port));
            yield break;
        }
    }

    // 与MatLab连接（异步协程版，不阻塞主线程）
    private bool connecting = false;   // 防并发重连
    System.Collections.IEnumerator LabelConnectAsync(string ip, int port)
    {
        // 如果已经在重连中，等它结束
        while (connecting) { yield return null; }
        connecting = true;
        try
        {
            // 清理旧连接（用空 try/catch 包裹，无 yield）
            try { if (stream != null) stream.Close(); } catch { }
            try { if (tcpClient != null) tcpClient.Close(); } catch { }
            stream = null;
            tcpClient = null;

            // --- 阶段1：发起异步连接（无 yield）---
            System.Threading.Tasks.Task connectTask = null;
            try
            {
                Debug.Log($"[PICO] 正在连接 {ip}:{port} ...");
                tcpClient = new TcpClient();
                connectTask = tcpClient.ConnectAsync(ip, port);
            }
            catch (System.Exception e)
            {
                Debug.LogError($"[PICO] ✗ 启动连接失败 {ip}:{port} - {e.GetType().Name}: {e.Message}");
                try { if (tcpClient != null) tcpClient.Close(); } catch { }
                tcpClient = null;
                yield break;
            }

            // --- 阶段2：轮询等待（带 yield）---
            float t = 0f;
            while (!connectTask.IsCompleted && t < 5f) { t += Time.unscaledDeltaTime; yield return null; }

            if (!connectTask.IsCompleted)
            {
                Debug.LogError($"[PICO] 连接超时 (5s): {ip}:{port}");
                try { tcpClient.Close(); } catch { }
                tcpClient = null;
                stream = null;
                yield break;
            }

            // --- 阶段3：检查结果（无 yield）---
            bool success = false;
            System.Exception err = null;
            try
            {
                if (connectTask.Exception != null) err = connectTask.Exception;
                else
                {
            stream = tcpClient.GetStream();
                    success = true;
                }
            }
            catch (System.Exception e) { err = e; }

            if (success)
            {
                Debug.Log($"[PICO] ✓ 已连接 {ip}:{port}");
            }
            else
            {
                Debug.LogError($"[PICO] ✗ 连接失败 {ip}:{port} - {err.GetType().Name}: {err.Message}");
                try { if (tcpClient != null) tcpClient.Close(); } catch { }
                tcpClient = null;
                stream = null;
            }
        }
        finally
        {
            connecting = false;
        }
    }

    // ===== 视频流：连接 + 收发循环（独立于 trigger TCP）=====
    IEnumerator VideoConnectAsync(string ip, int port)
    {
        // 清理旧连接
        try { if (videoStream != null) videoStream.Close(); } catch { }
        try { if (videoTcp != null) videoTcp.Close(); } catch { }
        videoStream = null;
        videoTcp = null;

        System.Threading.Tasks.Task connectTask = null;
        try
        {
            videoTcp = new TcpClient();
            // 关掉 Nagle，避免小包延迟（视频帧合成大包也要）
            videoTcp.NoDelay = true;
            connectTask = videoTcp.ConnectAsync(ip, port);
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("[PICO/Video] 启动连接失败: " + e.Message);
            yield break;
        }

        float t = 0f;
        while (!connectTask.IsCompleted && t < 5f) { t += Time.unscaledDeltaTime; yield return null; }

        if (!connectTask.IsCompleted || connectTask.Exception != null)
        {
            Debug.LogWarning("[PICO/Video] 连接失败: " + (connectTask.Exception != null ? connectTask.Exception.Message : "timeout"));
            try { if (videoTcp != null) videoTcp.Close(); } catch { }
            videoTcp = null;
            yield break;
        }

        try { videoStream = videoTcp.GetStream(); } catch { }
        if (videoStream != null)
        {
            Debug.Log($"[PICO/Video] ✓ 已连接 {ip}:{port}");
            videoFrameSeq = 0;
            lastAckSeq = -1;
        }
    }

    // 视频发送循环：30 fps 抓屏 → JPEG → 写出去（带长度头 + seq）
    IEnumerator VideoSendLoop()
    {
        // 准备 RenderTexture + Texture2D（GC 友好：只 new 一次）
        if (videoRT == null) videoRT = new RenderTexture(VideoWidth, VideoHeight, 24, RenderTextureFormat.Default);
        if (videoTex == null) videoTex = new Texture2D(VideoWidth, VideoHeight, TextureFormat.RGB24, false);

        // 等到 stream 可用
        while (videoStream == null) { yield return new WaitForSeconds(0.5f); }

        float interval = 1f / VideoFps;
        float nextTime = Time.unscaledTime;

        while (true)
        {
            // 控制帧率
            float wait = nextTime - Time.unscaledTime;
            if (wait > 0) yield return new WaitForSecondsRealtime(wait);
            nextTime += interval;

            // 如果 PC 端落后太多（>10 帧没确认），跳过这一帧
            int outstanding = videoFrameSeq - lastAckSeq - 1;
            if (outstanding > 10)
            {
                continue;  // 丢这一帧
            }

            // 抓当前主相机画面到 RenderTexture
            Camera cam = Camera.main;
            if (cam == null) { yield return new WaitForSeconds(0.1f); continue; }
            var prevTarget = cam.targetTexture;
            var prevActive = RenderTexture.active;
            try
            {
                cam.targetTexture = videoRT;
                cam.Render();
                RenderTexture.active = videoRT;
                videoTex.ReadPixels(new Rect(0, 0, VideoWidth, VideoHeight), 0, 0);
                videoTex.Apply(false);
            }
            catch (System.Exception e)
            {
                Debug.LogWarning("[PICO/Video] 抓屏失败: " + e.Message);
                cam.targetTexture = prevTarget;
                RenderTexture.active = prevActive;
                continue;
            }
            finally
            {
                cam.targetTexture = prevTarget;
                RenderTexture.active = prevActive;
            }

            // JPEG 编码
            byte[] jpg = null;
            try { jpg = videoTex.EncodeToJPG(Mathf.Clamp(VideoJpegQuality, 1, 100)); }
            catch (System.Exception e)
            {
                Debug.LogWarning("[PICO/Video] 编码失败: " + e.Message);
                continue;
            }
            if (jpg == null || jpg.Length == 0) continue;

            // 组装包：[VRVF][seq][len][jpg]
            int seq = videoFrameSeq++;
            byte[] header = new byte[12];
            VRVF_MAGIC.CopyTo(header, 0);
            int seq32 = seq;
            int len32 = jpg.Length;
            header[4]  = (byte)(seq32 & 0xFF);
            header[5]  = (byte)((seq32 >> 8) & 0xFF);
            header[6]  = (byte)((seq32 >> 16) & 0xFF);
            header[7]  = (byte)((seq32 >> 24) & 0xFF);
            header[8]  = (byte)(len32 & 0xFF);
            header[9]  = (byte)((len32 >> 8) & 0xFF);
            header[10] = (byte)((len32 >> 16) & 0xFF);
            header[11] = (byte)((len32 >> 24) & 0xFF);

            // 写头 + 数据（不阻塞：先 WriteAsync 头，再 WriteAsync 数据）
            if (videoStream == null) { yield return new WaitForSeconds(0.5f); continue; }

            // 把"会 yield"的循环和"会 throw"的 try 拆开
            // sendOk：true=这一帧发完，false=中途失败需要重连，null=丢帧
            bool? sendOk = false;
            string sendErr = null;
            try
            {
                var t1 = videoStream.WriteAsync(header, 0, header.Length);
                t1.Wait();                       // 头：同步等（小，4B+4B+4B）
                if (t1.IsFaulted) throw (t1.Exception ?? new System.Exception("header write faulted"));

                var t2 = videoStream.WriteAsync(jpg, 0, jpg.Length);
                // 数据大，最多等 0.5s；不让出主循环（避免 catch 里 yield）
                float waited = 0f;
                while (!t2.IsCompleted && waited < 0.5f)
                {
                    waited += Time.unscaledDeltaTime;
                    System.Threading.Thread.Sleep(1);
                }
                if (!t2.IsCompleted) { sendOk = null; }   // 超时：丢帧
                else if (t2.IsFaulted) throw (t2.Exception ?? new System.Exception("body write faulted"));
                else sendOk = true;
            }
            catch (System.Exception e)
            {
                sendOk = false;
                sendErr = e.Message;
            }

            if (sendOk == true) continue;        // 帧发完，下一帧
            if (sendOk == null) continue;        // 头/数据超时，丢这帧
            // sendOk == false：连接挂了，重连
            Debug.LogWarning("[PICO/Video] 发送异常（重连）: " + sendErr);
            try { if (videoStream != null) videoStream.Close(); } catch { }
            try { if (videoTcp != null) videoTcp.Close(); } catch { }
            videoStream = null;
            videoTcp = null;
            yield return new WaitForSeconds(1f);
            yield return StartCoroutine(VideoConnectAsync(IPAddress, VideoPort));
        }
    }

    // 启动/停止视频流（在 Start 里调一次开；Stop 时再关）
    public void StartVideoStream()
    {
        if (videoLoopRunning) return;
        videoLoopRunning = true;
        StartCoroutine(VideoConnectAsync(IPAddress, VideoPort));
        StartCoroutine(VideoSendLoop());
    }

    public void StopVideoStream()
    {
        videoLoopRunning = false;
        try { if (videoStream != null) videoStream.Close(); } catch { }
        try { if (videoTcp != null) videoTcp.Close(); } catch { }
        videoStream = null;
        videoTcp = null;
    }

    // ===== 旧 trigger 代码（保持不变）=====
    // 兼容旧调用（协程入口）
    void LabelConnect(string ip, int port)
    {
        StartCoroutine(LabelConnectAsync(ip, port));
    }

    // 动作提示
    IEnumerator MotionCue(int target)
    {
        //Vector3 newLArmRotation = LArm.transform.eulerAngles;
        //Vector3 newRArmRotation = RArm.transform.eulerAngles;
        Vector3 newPersonRotation = Person.transform.eulerAngles;
        Vector3 newHeadRotation = Head.transform.eulerAngles;

        switch(target)
        {
            case 2:  // 左侧
                newHeadRotation.x += -20f;
                newHeadRotation.y += 20f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;

            case 4:  // 上方灯
                newHeadRotation.x = -35f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;

            case 6:  // 右侧
                newHeadRotation.x += -30f;
                newHeadRotation.y += -30f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;

            case 8:  // 下面球
                newHeadRotation.x = 20f;
                Head.transform.eulerAngles = newHeadRotation;
                yield return new WaitForSeconds(2.5f);
                break;

        }

        //switch (target)
        //{
        //    case 1:  // 目标1
            
        //        newRArmRotation.x = 20f;
        //        newRArmRotation.y = 50f;
        //        RArm.transform.eulerAngles = newRArmRotation;  // 1步
        //        yield return new WaitForSeconds(0.1f);
        //        newRArmRotation.x = 40f;
        //        RArm.transform.eulerAngles = newRArmRotation;  // 2步
        //        yield return new WaitForSeconds(0.1f);
        //        newRArmRotation.x = 60f;
        //        RArm.transform.eulerAngles = newRArmRotation;  // 指

        //        yield return new WaitForSeconds(2.3f);
        //        break;

        //    case 2:  // 目标2

        //        newRArmRotation.x = 40f;
        //        newRArmRotation.y = 395f;
        //        RArm.transform.eulerAngles = newRArmRotation;  // 1步
        //        yield return new WaitForSeconds(0.1f);
        //        newRArmRotation.x = 70f;
        //        RArm.transform.eulerAngles = newRArmRotation;  // 2步
        //        yield return new WaitForSeconds(0.1f);
        //        newRArmRotation.x = 100f;
        //        RArm.transform.eulerAngles = newRArmRotation;  // 3步
        //        yield return new WaitForSeconds(0.1f);
        //        newRArmRotation.x = 120f;
        //        RArm.transform.eulerAngles = newRArmRotation;  // 指

        //        yield return new WaitForSeconds(2.2f);
        //        break;

        //    case 3:  // 目标3

        //        newLArmRotation.x = -80f;
        //        newLArmRotation.y = 140f;
        //        LArm.transform.eulerAngles = newLArmRotation;

        //        newPersonRotation.y = 0f;
        //        Person.transform.eulerAngles = newPersonRotation;  // 转身

        //        yield return new WaitForSeconds(2.2f);
        //        break;

        //    case 4:  // 目标4

        //        newLArmRotation.x = -160f;
        //        newLArmRotation.y = 190f;
        //        LArm.transform.eulerAngles = newLArmRotation;

        //        newPersonRotation.y = 0f;
        //        Person.transform.eulerAngles = newPersonRotation;  // 转身

        //        yield return new WaitForSeconds(2.2f);
        //        break;

        //    case 5:  // 目标5

        //        newRArmRotation.x = 100f;
        //        newRArmRotation.y = 400f;
        //        RArm.transform.eulerAngles = newRArmRotation;

        //        newPersonRotation.y = 0f;
        //        Person.transform.eulerAngles = newPersonRotation;  // 转身

        //        yield return new WaitForSeconds(2.2f);
        //        break;

        //    case 6:  // 目标6
        //        newLArmRotation.x = -50f;
        //        newLArmRotation.y = 50f;
        //        LArm.transform.eulerAngles = newLArmRotation;  // 1步
        //        yield return new WaitForSeconds(0.1f);
        //        newLArmRotation.x = -80f;
        //        newLArmRotation.y = 80f;
        //        LArm.transform.eulerAngles = newLArmRotation;  // 2步
        //        yield return new WaitForSeconds(0.1f);
        //        newLArmRotation.x = -100f;
        //        newLArmRotation.y = 100f;
        //        LArm.transform.eulerAngles = newLArmRotation;  // 3步
        //        yield return new WaitForSeconds(0.1f);
        //        newLArmRotation.x = -130f;
        //        newLArmRotation.y = 130f;
        //        LArm.transform.eulerAngles = newLArmRotation;
        //        yield return new WaitForSeconds(2.2f);
        //        break;

        //    case 7:  // 目标7
        //        newLArmRotation.x = -20f;
        //        LArm.transform.eulerAngles = newLArmRotation;  // 1步
        //        yield return new WaitForSeconds(0.1f);
        //        newLArmRotation.x = -40f;
        //        LArm.transform.eulerAngles = newLArmRotation;  // 2步
        //        yield return new WaitForSeconds(0.1f);
        //        newLArmRotation.x = -60f;
        //        LArm.transform.eulerAngles = newLArmRotation;  // 3步
        //        yield return new WaitForSeconds(0.1f);
        //        newLArmRotation.x = -80f;
        //        LArm.transform.eulerAngles = newLArmRotation;

        //        yield return new WaitForSeconds(2.2f);
        //        break;

        //    case 8:  // 目标8
        //        newLArmRotation.x = -60f;
        //        newLArmRotation.y = 180f;
        //        LArm.transform.eulerAngles = newLArmRotation;

        //        yield return new WaitForSeconds(2.5f);
        //        break;
            
        //}
    }

    void Update()
    {
        if (pressA.action.WasPerformedThisFrame() || pressX.action.WasPerformedThisFrame())
        {
            switch (mode)
            {
                case 1:
                    ExecuteMode1();
                    break;
                case 2:
                    ExecuteMode2();
                    break;
                case 3:
                    ExecuteMode3();
                    break;
            }
        }

    }

    void ExecuteMode1()
    {
        mode = 0;
        StartCoroutine(PPP1());

    }

    void ExecuteMode2()
    {
        mode = 0;
        StartCoroutine(PPP2());
    }

    void ExecuteMode3()
    {
        mode = 0;
        StartCoroutine(PPP3());
    }

}
