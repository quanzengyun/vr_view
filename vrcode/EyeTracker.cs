using System;
using System.Collections;
using System.Collections.Generic;
using TMPro;
using Unity.XR.PXR;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

public class EyeTracker : MonoBehaviour
{
    public TMP_Text ui_text;
    public GameObject Cube;

    private bool trackingSupported = false;
    private bool tracking = false;
    private EyeTrackingData eyeTrackingData;
    private Transform cameraOffset;
    private Transform playerTransform;

    private const float rayLength = 7f;
    float y_offset = 1.7f;  // y轴偏移量
    float x_offset = 0.2f;  // x轴偏移量

    // 储存目标物体序号的string
    public string targetIndicesString = "";
    private float nextSampleTime = 0f;
    private float sampleInterval = 0.05f;  // 设置采样间隔（1秒20帧）

    // Start is called before the first frame update
    void Start()
    {
        var trackingState = (TrackingStateCode)PXR_MotionTracking.WantEyeTrackingService();


        if (trackingState == TrackingStateCode.PXR_MT_SUCCESS)
        {

            EyeTrackingMode eyeTrackingMode = EyeTrackingMode.PXR_ETM_NONE;

            int supportedModesCount = 0;
            trackingState = (TrackingStateCode)PXR_MotionTracking.GetEyeTrackingSupported(ref trackingSupported, ref supportedModesCount, ref eyeTrackingMode);


            if (trackingSupported)
            {

                EyeTrackingStartInfo info = new EyeTrackingStartInfo();
                info.needCalibration = 1;
                info.mode = EyeTrackingMode.PXR_ETM_BOTH;
                trackingState = (TrackingStateCode)PXR_MotionTracking.StartEyeTracking(ref info);

            }
            else
            {
                ui_text.text = "Not Supported!!";
            }
        }
        else
        {
            ui_text.text = "Want Fail";
        }

        cameraOffset = GameObject.FindGameObjectWithTag("MainCamera").transform;
        playerTransform = GameObject.FindGameObjectWithTag("Player").transform;

        // 开始协程
        StartCoroutine(EyeSample());

    }

    IEnumerator EyeSample()
    {

        while (true)
        {
            if (trackingSupported && Time.time >= nextSampleTime)
            {
                nextSampleTime = Time.time + sampleInterval;  // 更新下一次采样时间

                EyeTrackingState eyeTrackingState = new EyeTrackingState();
                var trackingState = (TrackingStateCode)PXR_MotionTracking.GetEyeTrackingState(ref tracking, ref eyeTrackingState);

                if (tracking)
                {
                    EyeTrackingDataGetInfo getInfo = new EyeTrackingDataGetInfo();
                    getInfo.displayTime = 0;
                    getInfo.flags = EyeTrackingDataGetFlags.PXR_EYE_DEFAULT
                                    | EyeTrackingDataGetFlags.PXR_EYE_POSITION
                                    | EyeTrackingDataGetFlags.PXR_EYE_ORIENTATION;

                    trackingState = (TrackingStateCode)PXR_MotionTracking.GetEyeTrackingData(ref getInfo, ref eyeTrackingData);

                    // 打印眼动坐标
                    //ui_text.text = eyeTrackingData.ToString();

                    //相机的世界坐标
                    Transform root = cameraOffset.transform;
                    var eyeData = eyeTrackingData.eyeDatas[2].pose;

                    // 眼球相较于相机的位置
                    Vector3 position = new Vector3(eyeData.orientation.y, eyeData.orientation.z, -eyeData.orientation.w);
                    // 眼球相较于相机的旋转
                    Quaternion rotation = new Quaternion(-eyeData.position.x, -eyeData.position.y, eyeData.position.z, eyeData.orientation.x);

                    // 眼球的世界坐标
                    var origin = root.position + position;

                    Vector3 point = origin + playerTransform.rotation * rotation * Vector3.forward * rayLength;


                    // 位置修正
                    point.y -= y_offset;
                    point.x += x_offset;

                    Cube.transform.position = point;

                    // 打印随动球坐标
                    //ui_text.text = Cube.transform.position.ToString();

                    int targetID = GetTargetID(Cube.transform.position);
                    ui_text.text = targetID.ToString();  // 打印目标物体序号

                    targetIndicesString += targetID.ToString();  // 储存眼动序号
                }
            }

            yield return null;  // 等待下一帧
        }
    }

    private int GetTargetID(Vector3 position)
    {
        if (position.x <= 15.70 && position.x >= 13.60 && position.y <= 2.2 && position.y >= 0.55) return 1;
        if (position.x <= 13.9 && position.x >= 12.8 && position.y <= 4 && position.y >= 2.6) return 2;
        if (position.x <= 16.8 && position.x >= 15.75 && position.y <= 2.75 && position.y >= 1.25) return 3;
        if (position.x <= 18.4 && position.x >= 17.5 && position.y <= 4.25 && position.y >= 3) return 4;
        if (position.x <= 20.4 && position.x >= 19.45 && position.y <= 3.3 && position.y >= 1.5) return 5;
        if (position.x <= 22.7 && position.x >= 21.50 && position.y <= 4.1 && position.y >= 3) return 6;
        if (position.x <= 21.8 && position.x >= 20.5 && position.y <= 2.75 && position.y >= 0.9) return 7;
        if (position.x <= 18.8 && position.x >= 17.2 && position.y <= 0.32 && position.y >= -0.5) return 8;

        return 0; // (无注视)默认值
    }
}
