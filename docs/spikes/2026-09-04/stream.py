import time,sys
t=time.time(); i=0
while time.time()-t<4:
    i+=1; sys.stdout.write(f"stream {i} 0123456789abcdef 0123456789abcdef\n")
