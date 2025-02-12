# Extract features from images and save into "features_all.csv"

import os
import dlib
import csv
import numpy as np
import logging
import cv2

class extraction():
    global path_images_from_camera
    global detector
    global predictor
    global face_reco_model
    
    #  Path of cropped faces
    path_images_from_camera = "data/data_faces_from_camera/"

    #  Use frontal face detector of Dlib
    detector = dlib.get_frontal_face_detector()

    #  Get face landmarks
    predictor = dlib.shape_predictor('data/data_dlib/shape_predictor_68_face_landmarks.dat')

    #  Use Dlib resnet50 model to get 128D face descriptor
    face_reco_model = dlib.face_recognition_model_v1("data/data_dlib/dlib_face_recognition_resnet_model_v1.dat")


    #  Return 128D features for single image

    def return_128d_features(self,path_img):
        img_rd = cv2.imread(path_img)
        
        # Check if the image was loaded successfully
        if img_rd is None:
            logging.error("Failed to load image: %s", path_img)
            return 0  # Return 0 or an appropriate value to indicate failure
    
        faces = detector(img_rd, 1)

        logging.info("%-40s %-20s", " Image with faces detected:", path_img)

        # For photos of faces saved, we need to make sure that we can detect faces from the cropped images
        if len(faces) != 0:
            shape = predictor(img_rd, faces[0])
            face_descriptor = face_reco_model.compute_face_descriptor(img_rd, shape)
        else:
            face_descriptor = 0
            logging.warning("no face")
            
        return face_descriptor


    #   Return the mean value of 128D face descriptor for person X

    def return_features_mean_personX(self,path_face_personX):
        features_list_personX = []
        photos_list = os.listdir(path_face_personX)
        if photos_list:
            for i in range(len(photos_list)):
                #  return_128d_features()  128D  / Get 128D features for single image of personX
                # logging.info("%-40s %-20s", " / Reading image:", path_face_personX + "/" + photos_list[i])
                features_128d = self.return_128d_features(path_face_personX + "/" + photos_list[i])
               
                features_list_personX.append(features_128d)
        else:
            logging.warning(" Warning: No images in%s/", path_face_personX)

    
        if features_list_personX:
            features_mean_personX = np.array(features_list_personX, dtype=object).mean(axis=0)
        else:
            features_mean_personX = -1
        
        print(type(features_mean_personX))
        return features_mean_personX


    def main(self, user_id):
        logging.basicConfig(level=logging.INFO)
        person_list = os.listdir("data/data_faces_from_camera/")
        person_list.sort()

        with open(f"data/export/{user_id}.csv", "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            for person in person_list:
                print(person)
                logging.info("%sperson_%s", path_images_from_camera, person)
                features_mean_personX = self.return_features_mean_personX(path_images_from_camera + person)
                person_name = None
                # Check if features were successfully extracted
                if features_mean_personX is not -1:
                    if len(person.split('_', 2)) == 2:
                        person_name = person
                    else:
                        person_name = person.split('_', 2)[-1]
    
                    print("Person name :", person_name)
    
                    # Ensure features_mean_personX is a NumPy array before inserting
                    if isinstance(features_mean_personX, np.ndarray):
                        features_mean_personX = np.insert(features_mean_personX, 0, person_name, axis=0)
                    else:
                        # Handle case where features_mean_personX is not an array (e.g., -1)
                        # Initialize a new array with person_name and handle this case appropriately
                        features_mean_personX = np.array([person_name] + [0]*128)  # Example initialization
    
                    writer.writerow(features_mean_personX)
                else:
                    print(f"Failed to extract features for {person_name}. Skipping.")
    
            print(f"Save all the features of faces registered into: data/{user_id}.csv")
            logging.info(f"Save all the features of faces registered into: data/{user_id}.csv")
            

