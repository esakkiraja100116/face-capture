import dlib
from PIL import Image

# Load the image
image_path = 'img_face_1.jpg'
image = dlib.load_rgb_image(image_path)

# Initialize the face detector
detector = dlib.get_frontal_face_detector()

# Detect faces in the image
faces = detector(image)

# Now 'faces' contains a list of rectangles where faces were detected
# You can use this 'faces' object as a 'dlib.rectangles' type

# For example, if you want to print the coordinates of the first face detected:
if len(faces) > 0:
    print("First face coordinates: ", faces[0])
else:
    print("No faces detected in the image.")
