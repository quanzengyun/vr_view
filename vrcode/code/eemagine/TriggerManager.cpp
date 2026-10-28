#include "TriggerManager.h"
#include <QDebug>

TriggerManager::TriggerManager(QObject *parent) : QObject(parent) {
    m_serialPort = new QSerialPort(this);
}

TriggerManager::~TriggerManager() {
    if (m_serialPort && m_serialPort->isOpen()) {
        m_serialPort->close();
    }
}

// 初始化串口
bool TriggerManager::init(QString portName) {
    if (m_serialPort) { m_serialPort->close(); delete m_serialPort; }

    m_serialPort->setPortName(portName);
    m_serialPort->setBaudRate(QSerialPort::Baud57600); // 波特率
    m_serialPort->setDataBits(QSerialPort::Data8);  // 数据位表示标记
    m_serialPort->setParity(QSerialPort::NoParity);  // 校验位
    m_serialPort->setStopBits(QSerialPort::OneStop);  // 停止位
    m_serialPort->setFlowControl(QSerialPort::NoFlowControl);  // 流控
    
    if (m_serialPort->open(QIODevice::WriteOnly)) {
        qDebug() << "串口:" << portName << "打开成功!";
        return true;
    }else {
        qDebug() << "串口打开失败:" << m_serialPort->errorString();
        return false;
    }
}

// 发送标记
void TriggerManager::sendMarker(quint8 tag) {
    if (!m_serialPort || !m_serialPort->isOpen()) return;

    // 1. 发送实际 Tag
    m_serialPort->write(reinterpret_cast<const char*>(&tag), 1);
    m_serialPort->flush();

    // 2. 脉冲宽度保持
    QThread::msleep(4);

    // 3. 复位发送0
    quint8 reset = 0;
    m_serialPort->write(reinterpret_cast<const char*>(&reset), 1);
    m_serialPort->flush();

}