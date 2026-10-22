function [address]=Init_trigger(my_LPT)
address = hex2dec(my_LPT); 
config_io;
global cogent;
if( cogent.io.status ~= 0 )
   error('inp/outp installation failed');
end
outp(address,0);