import dlib
import cv2

def detect_face(image_path):
    
    # Load the image
    image = cv2.imread(image_path)
    # Convert the image to grayscale
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Perform histogram equalization to improve contrast
    equalized_image = cv2.equalizeHist(gray_image)

    # Initialize face detector
    face_detector = dlib.get_frontal_face_detector()
    # Detect faces in the image
    faces = face_detector(equalized_image)
    # Return True if faces were detected, False otherwise
    if len(faces) > 0:
        return True
    else:
        return False

# Example usage:
image_path = "/var/www/face/face/data/data_faces_from_camera/person_1_test32raja/129014e8cab44ba49d6815f3865559d0.jpg"

if detect_face(image_path):
    print("Face detected!")
else:
    print("No face detected.")