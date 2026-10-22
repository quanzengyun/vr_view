

% % 初始化文本文件
% clc;
% clear;
% close all;



filePath = 'output_log.txt';
fileInfo = dir(filePath);
% if fileInfo.bytes > 0 
%     error '姐姐 ，你忘记改文件名字了！';
% end

fileID = fopen('output_log.txt', 'w');  % 打开文件，准备写入

% 初始化并口（LPT）地址和触发器
% usrpar.my_LPT = '0378';  % 并口地址
% usrpar.address = Init_trigger(usrpar.my_LPT);  % 初始化并口
%
portName = 'COM5';   % 串口名，例如 COM3、COM4
baudRate = 57600;   % 波特率，需和接收端一致
% 创建串口对象

if strcmp(serial_handle.Status, 'open')
    
else
    serial_handle = serial(portName, 'BaudRate', baudRate);
    fopen(serial_handle);   % 打开串口
end
%%                                                                             
% 设置 TCP 服务器
tcpServer = tcpip('0.0.0.0', 12345, 'NetworkRole', 'server');
set(tcpServer, 'InputBufferSize', 1024);
disp('等待连接....');
fopen(tcpServer); 
disp('连接成功.');
    
Nice = true; 
receivedCounts = 1;
allMessages = {};  % 用于存储所有接收的信息
buffer = '';  % 缓存未处理的消息
currentRecord = '';  % 用于暂存当前记录的数据

while Nice
    if tcpServer.BytesAvailable > 0
        receivedData = fread(tcpServer, tcpServer.BytesAvailable);
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
    %     异常捕获，退出while
    try 
        fwrite(tcpServer, 12, 'uint8');
    catch ERR
        
        disp(['连接断开', ERR.message]);
        break;
    end
end

% 确保在退出循环之前，将最后一条记录写入文件
if ~isempty(currentRecord)
    fprintf(fileID, '%s\n', currentRecord);  % 写入最后的记录
end


% 关闭文件
fclose(serial_handle);
fclose(tcpServer);
fclose(fileID);
delete(serial_handle);
disp(['close']);

