using System;
using System.Collections;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Text;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.UI;
using UnityEngine.XR.Interaction.Toolkit;
using Unity.XR.PXR;

public class ButtonRecord : MonoBehaviour
{

    // 定义手柄按键
    public InputActionReference pressB;  // 右手柄B键
    public InputActionReference pressY;  // 右手柄Y键

    // 储存目标物体序号的string
    public string buttonRecordString = "";
    private float nextSampleTime = 0f;
    private float sampleInterval = 0.0125f;  // 设置采样间隔（1秒40帧）

    // Start is called before the first frame update
    void Start()
    {
        // 开始协程
        StartCoroutine(Recorder());
    }

    IEnumerator Recorder()
    {
        while (true)
        {
            if (Time.time >= nextSampleTime)
            {
                nextSampleTime = Time.time + sampleInterval;  // 更新下一次采样时间

                if (pressB.action.WasPerformedThisFrame() || pressY.action.WasPerformedThisFrame())
                {
                    buttonRecordString += "Y";
                }
                else
                {
                    buttonRecordString += "n";
                }
            }

            yield return null;  // 等待下一帧
        }
    }

}
