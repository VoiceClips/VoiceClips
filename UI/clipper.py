import os
import time
from collections import deque
import threading
import subprocess
import vosk
import pyaudio
from rapidfuzz import fuzz
import platform
import wave
import requests
import zipfile
import shutil
from pathlib import Path

class Clipper:
    DEFAULT_MODEL_NAME = "vosk-model-small-en-us-0.15"
    MODEL_URL = f"https://alphacephei.com/vosk/models/{DEFAULT_MODEL_NAME}.zip"
    
    def __init__(self, buffer_duration=30, output_folder="clips", format="mp4", model_path=None):
        self.buffer_duration = buffer_duration
        self.output_folder = os.path.expanduser(output_folder)
        self.format = format.lower()
        self.is_listening = False
        self.frame_buffer = None
        self.audio_buffer = None
        self.clip_counter = 0
        self.lock = threading.Lock()

        # Create output folder if it doesn't exist
        os.makedirs(self.output_folder, exist_ok=True)

        # Initialize the model path
        self.model_path = self._initialize_model(model_path)
        
        try:
            self.model = vosk.Model(self.model_path)
            self.audio_stream = pyaudio.PyAudio()
        except Exception as e:
            raise Exception(f"Failed to initialize: {str(e)}")

    def _initialize_model(self, model_path=None):
        """Initialize and return the appropriate model path, downloading if necessary."""
        if model_path and os.path.isdir(model_path):
            return model_path

        # Define default paths
        app_data_dir = self._get_app_data_dir()
        default_model_path = os.path.join(app_data_dir, "vosk_models", self.DEFAULT_MODEL_NAME)

        # If model doesn't exist in default location, download it
        if not os.path.isdir(default_model_path):
            print(f"Vosk model not found. Downloading to {default_model_path}...")
            self._download_and_extract_model(default_model_path)

        return default_model_path

    def _get_app_data_dir(self):
        """Get the appropriate application data directory for the current platform."""
        system = platform.system()
        if system == "Windows":
            app_data = os.getenv("APPDATA")
            return os.path.join(app_data, "Clipper")
        elif system == "Darwin":  # macOS
            return os.path.expanduser("~/Library/Application Support/Clipper")
        else:  # Linux and others
            return os.path.expanduser("~/.clipper")

    def _download_and_extract_model(self, model_path):
        """Download and extract the Vosk model."""
        try:
            # Create temporary directory for download
            temp_dir = os.path.join(self._get_app_data_dir(), "temp")
            os.makedirs(temp_dir, exist_ok=True)
            zip_path = os.path.join(temp_dir, f"{self.DEFAULT_MODEL_NAME}.zip")

            # Download the model
            print("Downloading Vosk model...")
            response = requests.get(self.MODEL_URL, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
            
            with open(zip_path, 'wb') as f:
                for data in response.iter_content(block_size):
                    f.write(data)

            # Extract the model
            print("Extracting model...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(os.path.dirname(model_path))

            # Clean up
            os.remove(zip_path)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

            print("Model downloaded and extracted successfully!")

        except Exception as e:
            raise Exception(f"Failed to download model: {str(e)}")

    def set_buffers(self, frame_buffer, audio_buffer):
        """Set the frame and audio buffers from the main app"""
        self.frame_buffer = frame_buffer
        self.audio_buffer = audio_buffer

    def set_save_location(self, folder_path):
        """Set the save directory for clips."""
        self.output_folder = folder_path
        if not os.path.exists(self.output_folder):
            os.makedirs(self.output_folder)

    def set_file_format(self, format):
        """Set the file format for clips (mp4, mov, mkv, etc.)."""
        self.format = format.lower()
    
    def set_buffer_duration(self, duration):
        """Set the buffer duration in seconds"""
        self.buffer_duration = duration
        # Update buffer sizes
        if hasattr(self, 'frame_buffer'):
            self.frame_buffer = deque(maxlen=duration * 30)  # 30 fps
        if hasattr(self, 'audio_buffer'):
            self.audio_buffer = deque(maxlen=duration * 44100 * 2)  # 44.1kHz stereo

    def save_clip(self):
        """Save the buffered content as a clip"""
        with self.lock:
            if not self.frame_buffer or len(self.frame_buffer) == 0:
                print("No frames to save")
                return

            self.clip_counter += 1
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            
            # Create output filename and temporary files
            output_filename = os.path.join(
                self.output_folder, 
                f"clip_{timestamp}_{self.clip_counter}.{self.format.lower()}"
            )
            temp_video = os.path.join(self.output_folder, f"temp_video_{timestamp}.raw")
            temp_audio = os.path.join(self.output_folder, f"temp_audio_{timestamp}.wav")
            
            print(f"Saving clip to: {output_filename}")

            try:
                # Save buffered video frames
                with open(temp_video, 'wb') as f:
                    frames = list(self.frame_buffer)
                    for frame in frames:
                        f.write(frame)

                # Save buffered audio
                if self.audio_buffer and len(self.audio_buffer) > 0:
                    with wave.open(temp_audio, 'wb') as wf:
                        wf.setnchannels(2)
                        wf.setsampwidth(2)
                        wf.setframerate(44100)
                        wf.writeframes(b''.join(list(self.audio_buffer)))

                # Create FFmpeg command for processing the buffered data
                system = platform.system()
                
                if system == "Darwin":  # macOS
                    command = [
                        "ffmpeg",
                        "-f", "rawvideo",
                        "-vcodec", "rawvideo",
                        "-s", "1920x1080",  # Make sure this matches your capture resolution
                        "-pix_fmt", "rgb24",
                        "-framerate", "30",
                        "-i", temp_video
                    ]

                    if os.path.exists(temp_audio):
                        command.extend([
                            "-i", temp_audio,
                            "-c:a", "aac",
                            "-b:a", "192k"
                        ])

                    command.extend([
                        "-c:v", "h264",
                        "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p",
                        "-profile:v", "high",
                        "-r", "30",
                        "-movflags", "+faststart",
                        "-strict", "experimental",
                        "-y",
                        output_filename
                    ])
                else:  # Windows/Linux
                    command = [
                        "ffmpeg",
                        "-f", "rawvideo",
                        "-vcodec", "rawvideo",
                        "-s", "1920x1080",
                        "-pix_fmt", "rgb24",
                        "-framerate", "30",
                        "-i", temp_video
                    ]

                    if os.path.exists(temp_audio):
                        command.extend([
                            "-i", temp_audio,
                            "-c:a", "aac",
                            "-b:a", "192k"
                        ])

                    command.extend([
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p",
                        "-profile:v", "baseline",
                        "-level", "3.0",
                        "-r", "30",
                        "-y",
                        output_filename
                    ])

                print(f"Processing clip...")
                result = subprocess.run(command, check=True, capture_output=True, text=True)
                print(f"Clip saved successfully: {output_filename}")
                
            except subprocess.CalledProcessError as e:
                print(f"Error saving clip: {e}")
                if e.stderr:
                    print(f"FFmpeg error: {e.stderr}")
            except Exception as e:
                print(f"Error saving clip: {e}")
            finally:
                # Clean up temporary files
                if os.path.exists(temp_video):
                    os.remove(temp_video)
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)

    def listen_for_clips(self):
        """Continuously listens for clip commands."""
        try:
            # Configure audio stream for optimal voice recognition
            stream = self.audio_stream.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=2048,  # Reduced buffer size for better responsiveness
                input_device_index=None,  # Use default input device
                stream_callback=None
            )
            
            recognizer = vosk.KaldiRecognizer(self.model, 16000)
            print("\nVoice Command System Active - Listening for 'clip' command...")
            print("----------------------------------------")
            
            self.is_listening = True
            recent_commands = deque(maxlen=3)  # Store recent commands to prevent duplicates
            last_clip_time = 0  # To prevent multiple clips in quick succession

            while self.is_listening:
                try:
                    data = stream.read(2048, exception_on_overflow=False)
                    if recognizer.AcceptWaveform(data):
                        result = eval(recognizer.Result())
                        recognized_text = result.get("text", "").lower().strip()
                        
                        # Only process if we have meaningful text
                        if recognized_text and len(recognized_text) > 1:
                            # Clean up the text
                            cleaned_text = ' '.join(
                                word for word in recognized_text.split()
                                if len(word) > 1 and  # Ignore single characters
                                word not in ['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to']  # Ignore common words
                            )
                            
                            if cleaned_text:
                                print(f"Heard: {cleaned_text}")
                                
                                # Check for clip command
                                current_time = time.time()
                                clip_words = ['clip', 'clips', 'clipped', 'click', 'quick', 'cli', 'clis']
                                
                                # Direct match check
                                clip_detected = any(word in cleaned_text for word in clip_words)
                                
                                # Fuzzy match check if no direct match
                                if not clip_detected:
                                    for word in cleaned_text.split():
                                        ratio = fuzz.ratio("clip", word)
                                        if ratio > 40:  # High threshold for accuracy
                                            clip_detected = True
                                            print(f"Fuzzy match: '{word}' matches 'clip' with {ratio}% confidence")
                                            break
                                
                                # Process clip command if detected
                                if clip_detected:
                                    # Check if enough time has passed since last clip
                                    if current_time - last_clip_time > 2.0:  # 2-second cooldown
                                        if cleaned_text not in recent_commands:
                                            print("\n🎬 Clip command detected! Creating clip...\n")
                                            threading.Thread(target=self.save_clip).start()
                                            last_clip_time = current_time
                                            recent_commands.append(cleaned_text)
                                        else:
                                            print("Duplicate command ignored")
                                    else:
                                        print("Please wait before creating another clip")

                except Exception as e:
                    print(f"Error processing audio: {e}")
                    
        except Exception as e:
            print(f"Error in speech recognition: {e}")
        finally:
            if 'stream' in locals():
                stream.stop_stream()
                stream.close()
            print("\nVoice Command System Stopped")

    def start_listening(self):
        """Starts the speech recognition in a new thread."""
        threading.Thread(target=self.listen_for_clips, daemon=True).start()

    def stop_listening(self):
        """Stops listening for speech commands."""
        self.is_listening = False