function serial_sendLabel(handle, label)
% label = 0x1f;
head = uint8(00);
label_self = [head,label]; 
for i = 1:2
    fwrite(handle,label_self(i),'uint8');
%     pause(1/Fs);                                      % 模拟实际采样周期（1ms）
end


end