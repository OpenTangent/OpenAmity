import re
import os
import mimetypes

text = "I created a file at /home/amity/Documents/OpenAmity/test.png, and also ~/Documents/audio.wav. Here is a bad one /home/amity/Documents/OpenAmity/test.png."
potential_paths = re.findall(r'(?:/|~)[^\s\'"<>\|]+', text)
print("Found potentials:", potential_paths)
for path in potential_paths:
    while path and path[-1] in '.,;:!?)':
        path = path[:-1]
    exp_path = os.path.expanduser(path)
    print("Processed:", exp_path)
