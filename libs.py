import os
from werkzeug.utils import secure_filename
import uuid
from PIL import Image

class libs():
    
    def save_images(self, folder, user_id, images):    
        name = self.check_duplicate(user_id)
        if name == False:
            user_folder = folder + self.take_latest_count() + str(user_id)
            os.makedirs(user_folder, exist_ok=True)
        else:
            user_folder = name
        
        image_index = 1
        for image in images:
            filename = secure_filename(f"{uuid.uuid4().hex}.jpg")
            # Save the image to a temporary file to get its size
            temp_path = os.path.join(user_folder, filename)
            print("Temp path :", temp_path)
            
            image.save(temp_path)
                
            # Get the size of the image file
            file_size = os.path.getsize(temp_path)
            file_size_kb = file_size / 1024

            print(f"Image {image_index} size: {file_size_kb} KB")
            image_index += 1
        
        return user_folder
    
    def take_latest_count(self,):
        folder_path = "/var/www/face/face/data/data_faces_from_camera/"  # Update this with the path to your folder

        # Get the list of files in the folder
        files = os.listdir(folder_path)

        return str(len(files) + 1) + "_"
    
    def check_duplicate(self,user_id):
        folder_path = "/var/www/face/face/data/data_faces_from_camera/"  # Update this with the path to your folder
        old_name = False
        # Get the list of files in the folder
        files = os.listdir(folder_path)
        for file_name in files:
            parts = file_name.split("_")
            e_user_id = parts[2]
            
            
            if e_user_id == user_id:
                old_name = folder_path + file_name
                break        
        
        return old_name
            
