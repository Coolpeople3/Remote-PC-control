###How to use it
Copy receiver.py to the other PC and run python receiver.py.
Run python controller.py on your PC.


##Dependencies: each script installs what it needs (mss, pillow, pynput on the receiver, pygame on the controller) the first time it runs.
Finding the IP: the receiver listens for a broadcast on your network, and the controller sends one and connects to whichever receiver answers. If several answer, it lists them so you can pick. If none answer, it asks for an IP, and you can also pass one directly with python controller.py 192.168.x.x.


##The one-time PIN

The receiver prints a 6-digit pairing PIN when it starts. The first time you connect, type it into the controller, which saves it, so every later run is hands-free. I kept this step on purpose. Without it, anyone on your Wi-Fi who ran the controller could take over the other PC’s mouse and watch its screen. The PIN is never sent over the network, only a hash proof of it.

##if there's library issues, the commands are:
- Receiver PC: pip install mss pillow pynput
- Your PC: pip install pygame
