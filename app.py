import argparse
from flask import Flask, render_template, request
from werkzeug.utils import secure_filename
import socket
from functools import wraps
from libs import libs
from datetime import datetime
import os
import shutil
from extraction_face_to_csv import extraction
from attendance_taker import Face_Recognizer
import uuid
import tempfile
import dlib
import cv2

app = Flask(__name__)

'''
1. Register api with image unique name [done]
2. Update images and delete csv then create new for all images with old one [check]
3. Attendance -> user_id, img => True or False
4. Delete -> user_id => img, csv [done]
'''

UPLOAD_FOLDER = 'data/data_faces_from_camera/person_'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 16MB limit
lib = libs()

# util function
def validUser(user_id):
    
    if not user_id:
        return False
    userExists = False
    person_list = os.listdir("data/export/")
    for person in person_list:
        if person == user_id+".csv":
            userExists = True
            break
    if not userExists:
        return False
    return True

def handle_exceptions(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # Call the decorated function
            return func(*args, **kwargs)
        except Exception as e:
            # Handle the exception and return a custom response
            error_message = "An error occurred: {}".format(str(e))
            return {"error": error_message}, 500
    return wrapper


def detect_face(image_path):
    # # Load the image
    image_cv = cv2.imread(image_path)
    # Convert the image to grayscale
    gray_image = cv2.cvtColor(image_cv, cv2.COLOR_BGR2GRAY)
    # Perform histogram equalization to improve contrast
    image = cv2.equalizeHist(gray_image)
    face_detector = dlib.get_frontal_face_detector()
    
    # Detect faces in the image
    faces = face_detector(image)
    
    # Return True if faces were detected, False otherwise
    if len(faces) > 0:
        return True
    else:
        return False
    
@app.route('/')
def index():
    return {
        'success' : True,
        'message' : 'Hello world'
    }, 200

@app.route('/upload', methods=['POST'])
def upload_images():
    user_id = request.form.get('user_id')
    if user_id is None:
        return {
            'success': False,
            'message': 'User ID not received!'
        }, 400

    images = []
    for i in range(1, 5):
        image = request.files.get(f'image{i}')
        
        if image is None:
            return {
                'success': False,
                'message': f'Image{i} not received!'
            }, 400
            
        else:
           
            # If a face is detected, append the image to the images list
            images.append(image)
                    
    print("saving images ")
    folder = lib.save_images(UPLOAD_FOLDER,user_id, images)
    print(folder)
    print("after saving the image")

    '''
    1. loop the folder 
    2. pass the each image actual image path to detect_face(image_path)
    3. if true return image uploaded successfully
    4. if false delete the folder and give the appropirate return
    '''    
     # Loop through the saved images in the folder
    image_paths = [os.path.join(folder, filename) for filename in os.listdir(folder)]
    all_faces_detected = True
    for image_path in image_paths:
        print("Image path loop")
        
        if not detect_face(image_path):
            print("Face not deteched")
            all_faces_detected = False
            break

    if all_faces_detected:
        print("Trying to extracting the face")
        extract_face = extraction()
        extract_face.main(user_id) 
        return {
            'success': True,
            'message': 'Images uploaded successfully',
        }, 200
    else:
        # If any image does not contain a face, delete the folder and return an error
        # shutil.rmtree(folder)
        return {
            'success': False,
            'message': 'Face not detected in given images.',
        }, 400


@app.route('/delete_user', methods=['DELETE'])
def delete_userid():
    user_id = request.args.get('user_id')  # Extract user_id from query parameters
    if user_id is None:
        return {
            'success': False,
            'message': 'User ID is not provided!'
        }, 400  # Bad request if user_id is not provided

    folder_path = "data/data_faces_from_camera/"  # Update this with the path to your folder
    export_path = f"data/export/{user_id}.csv" 
    # Get the list of files in the folder
    files = os.listdir(folder_path)
    deleted_files = []

    for file_name in files:
        start = file_name.find("_",7)
        e_user_id = file_name[start+1:]

        if e_user_id == user_id:
            folder_path = os.path.join(folder_path, file_name)
            shutil.rmtree(folder_path) 
            deleted_files.append(file_name)

    if deleted_files:
        os.remove(export_path)
        return {
            'success': True,
            'message': f'Files for user ID : {user_id} deleted!'
        }, 200
    else:
        return {
            'success': False,
            'message': f'No files found for user ID : {user_id} or Invalid userID!'
        }, 400

@app.route('/take_attendance', methods=['POST'])
def take_attendance():
    user_id = request.form.get('user_id')
    if user_id is None:
        return {
            'success': False,
            'message': 'User ID not received!'
        }, 400
        
    images = request.files.getlist('image')
        
    if not validUser(user_id):
        return {
            'success':False,
            'message':'Please provide a valid userID'
        }, 400
    # Check if only one image is provided
    if len(images) != 1:
        return {
            'success': False,
            'message': 'Exactly one image should be provided'
        }, 400

    image = images[0]  # Get the first image
    

    # Generate unique filename with UUID
    unique_filename = str(uuid.uuid4()) + os.path.splitext(image.filename)[1]
    save_path = os.path.join('data', 'check', unique_filename)

    # Save the image
    image.save(save_path)
    os.chmod(save_path, 0o777)  # 0o444 represents read permission for all users
    Face_Recognizer_con = Face_Recognizer()
    result = Face_Recognizer_con.run(user_id,unique_filename)
    
    print(result,"done")
    if result == False or not result:
        return {
            'success': False,
            'message': 'user not found',
        }, 400
    else:
        return {
            'success': True,
            'message': 'User_ID and the capture Matched!!'
        }, 200
        


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the Flask app with specified host and port.')
    parser.add_argument('--host', default='0.0.0.0', help='Host IP address to run the server on.')
    parser.add_argument('--port', type=int, default=5001, help='Port number to run the server on.')
    args = parser.parse_args()

    app.run(host=args.host, port=args.port)