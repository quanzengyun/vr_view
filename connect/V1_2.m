% 0.0161
% 初始化文本文件
% clc;
% clear;
% close all;

fileID = fopen('output_log.txt', 'w');  % 打开文件，准备写入

% 初始化并口（LPT）地址和触发器
% usrpar.my_LPT = '0378';  % 并口地址
% usrpar.address = Init_trigger(usrpar.my_LPT);  % 初始化并口
%%
portName = 'COM4';   % 串口名，例如 COM3、COM4
baudRate = 57600;   % 波特率，需和接收端一致
% 创建串口对象
serial_handle = serial(portName, 'BaudRate', baudRate);
fopen(serial_handle);   % 打开串口

%%                                                                             
% 设置 TCP 服务器
tcpServer = tcpip('0.0.0.0', 12345, 'NetworkRole', 'server');
set(tcpServer, 'InputBufferSize', 1024);

disp('等待客户端连接.....');
fopen(tcpServer); 
disp('连接成功。');
Nice = true; 
receivedCounts = 1;
allMessages = {};  % 用于存储所有接收的信息
buffer = '';  % 缓存未处理的消息
currentRecord = '';  % 用于暂存当前记录的数据

while Nice
    try
        % 检查客户端是否仍然连接（通过 Status 属性）
        if ~strcmp(tcpServer.Status, 'open')
            disp('客户端连接已断开。');
            break;
        end

        if tcpServer.BytesAvailable > 0
            try
                receivedData = fread(tcpServer, tcpServer.BytesAvailable);
            catch readErr
                disp('读取数据失败，客户端可能已异常退出。');
                disp(['错误信息: ', readErr.message]);
                break;
            end
            receivedMessage = char(receivedData');

            % 将新接收到的数据添加到缓存中
            buffer = [buffer, receivedMessage];

            % 以结尾处 "\n" 分割消息
            while contains(buffer, newline)
                % 按照 "\n" 分割消息
                [currentMessage, buffer] = strtok(buffer, newline);

                disp(['第 ', num2str(receivedCounts), ' 次数据接收:']);
                disp(currentMessage);

                if ~isempty(currentMessage)
                    % 检测是否以 'G' 或 'X' 开头
                    if startsWith(currentMessage, 'G') || startsWith(currentMessage, 'X')
                        % 如果 currentRecord 不是空的，则写入到文件
                        if ~isempty(currentRecord)
                            fprintf(fileID, '%s\n', currentRecord);  % 写入当前记录并换行
                        end

                        % 开始新的记录，格式为 '第几次'
                        currentRecord = sprintf('第 %d 次: %s', receivedCounts, currentMessage);  
                    else
                        % 对非 'G' 或 'X' 开头的记录追加内容，并使用 '-' 分隔
                        currentRecord = sprintf('%s - %s', currentRecord, currentMessage);
                    end

                    % 并口输出逻辑
                    if startsWith(currentMessage, 'X') && length(currentMessage) >= 2
                        % 处理以 'X' 开头的消息，将第二个字符重复并拼接成双字符
                        repeatedValueStr = repmat(currentMessage(2), 1, 2);  % 将第二个字符重复两次，形成 '44'
                        tagValue = str2double(repeatedValueStr);  % 将拼接后的字符串转换为数字值 (44)

                        % 将处理后的值通过并口发送一次
    %                     outp_mine(usrpar.address, tagValue);  % 发送一次合并后的值 44
    %                     串口打标
                        serial_sendLabel(serial_handle, tagValue);
                        disp(['发送拼接后的值: ', num2str(tagValue)]);

                    elseif startsWith(currentMessage, 'G') && length(currentMessage) >= 2
                        % 处理以 'G' 开头的消息，发送一次第二个字符的 ASCII 值
                        tagValue = double(currentMessage(2))-48;  % 获取第二个字符的 ASCII 值
    %                     outp_mine(usrpar.address, tagValue);  % 发送一次
    %                     串口打标
                        serial_sendLabel(serial_handle, tagValue);
                        disp(['发送一次第二个字符的 ASCII: ', num2str(tagValue)]);
                    end

                    % 第三种数据：检测是否包含 Y，若包含则发送 Y 的 ASCII 值
                    if contains(currentMessage, 'Y')
    %                     outp_mine(usrpar.address, double('Y'));  % 发送 Y 的 ASCII 值
    %                     串口打标
                        serial_sendLabel(serial_handle, 89);
                        disp('发送 Y 的 ASCII 值: 89');
                    end

                    % 将消息保存到 allMessages 数组中
                    allMessages{receivedCounts} = currentMessage;
                    receivedCounts = receivedCounts + 1;

                    % 每8次数据写入记录并换行
                    if mod(receivedCounts - 1, 8) == 0
                        fprintf(fileID, '%s\n', currentRecord);  % 写入当前记录
                        currentRecord = '';  % 重置当前记录
                    end
                else
                    disp('接收到的消息为空，跳过处理');
                end
            end
        end
%         pause(0.01);  % 防止 CPU 占用过高
        catch loopErr
        %=========
        disp('发生未知错误，退出主循环。');
        disp(['错误信息: ', loopErr.message]);
        break;
        %=========
    end
end

% 确保在退出循环之前，将最后一条记录写入文件
if ~isempty(currentRecord)
    fprintf(fileID, '%s\n', currentRecord);  % 写入最后的记录
end

% 关闭文件
fclose(serial_handle);
delete(serial_handle);
fclose(fileID);
