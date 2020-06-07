# -*- coding: utf-8 -*-
# @Author Michael Pavlov

import os
import telebot
from flask import Flask, request
import mysql.connector
from mysql.connector import Error
from mysql.connector import pooling
import logging
import time
import sys
import config #TODO need to remove for heroku env
from logging.handlers import RotatingFileHandler
import herokutelegramnodups
import datetime
import pytesseract
import re
import requests
import json
from docxtpl import DocxTemplate, RichText, Listing
import threading


VERSION = "o. 1.11"

class OCRBot:

    def __init__(self, env = 'heroku', mode = 'online', proxy=False):

        self.env = env

        self.logger = logging.getLogger("ocr_bot")
        self.logger.setLevel(logging.DEBUG)

        if self.env == 'heroku':
            handler = logging.StreamHandler(sys.stdout)
            # handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(name)s: %(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

            self.TG_BOT_TOKEN = os.environ['TOKEN']
            self.HEROKU_NAME = "ocr-bot" # TODO change this
            self.DB_USER = os.environ['DB_USER']
            self.DB_PASSWORD = os.environ['DB_PASSWORD']
            self.DB_HOST = os.environ['DB_HOST']
            self.DB_PORT = os.environ['DB_PORT']
            self.DB_DATABASE = "bots"
            self.ADMIN_ID = os.environ['ADMIN_ID']

            self.bot = telebot.TeleBot(self.TG_BOT_TOKEN)

            # Настройка Flask
            self.server = Flask(__name__)
            self.TELEBOT_URL = 'telebot_webhook/'
            self.BASE_URL = "https://" + self.HEROKU_NAME + ".herokuapp.com/"

            self.server.add_url_rule('/' + self.TELEBOT_URL + self.TG_BOT_TOKEN, view_func=self.process_updates,
                                     methods=['POST'])
            self.server.add_url_rule("/", view_func=self.webhook)

        elif self.env == 'local':
            handler = RotatingFileHandler("ocr_bot.log", mode='a', encoding='utf-8', backupCount=5,
                                     maxBytes=1 * 1024 * 1024)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

            self.TG_BOT_TOKEN = config.TG_BOT_TOKEN
            self.DB_USER = config.DB_USER
            self.DB_PASSWORD = config.DB_PASSWORD
            self.DB_HOST = config.DB_HOST
            self.DB_PORT = config.DB_PORT
            self.DB_DATABASE = config.DB_DATABASE
            self.ADMIN_ID = config.ADMIN_ID
            self.processing_interval_sec = config.PROCESSING_INTERVAL_SEC

            self.bot = telebot.TeleBot(self.TG_BOT_TOKEN)
            if proxy:
                telebot.apihelper.proxy = config.PROXY
        else:
            print("OCRBot() exit. Unknown environment:" + str(env))
            quit()

        self.duplicate_controll = herokutelegramnodups.HerokuTgNoDups(lifetime_seconds=2)

        self.inline_command_understand = ["Понятно"]
        self.markup_command_legal_list = "Список юр.лиц"
        self.settings_edit_pd = "Редактировать данные"
        self.settings_delete_pd = "Удалить данные"
        self.settings_commands = [self.settings_edit_pd, self.settings_delete_pd]

        self.rates_button_like_caption = "Супер"
        self.rates_button_soso_caption = "Так себе"
        self.rates_button_dislike_caption = "Говно"
        self.rates_button_bullshit_caption = "Полное говно"
        self.rates_button_long_caption = "Это было долго"
        self.rates_button_verylong_caption = "Не помню, общались долго"
        self.inline_rates_buttons = [self.rates_button_like_caption,
                                     self.rates_button_soso_caption,
                                     self.rates_button_dislike_caption,
                                     self.rates_button_bullshit_caption,
                                     self.rates_button_long_caption,
                                     self.rates_button_verylong_caption]

        self.risk_high_icon = '🔴'
        self.risk_medium_icon = '🟡'
        self.risk_low_icon = '🟢'

        # привязываем хенделер сообщений к боту:
        self.bot.set_update_listener(self.handle_messages)
        handler_dic = self.bot._build_handler_dict(self.handle_callback_messages)
        # привязываем хенделер колбеков inline-клавиатуры к боту:
        self.bot.add_callback_query_handler(handler_dic)

        try:
            self.connection_pool = mysql.connector.pooling.MySQLConnectionPool(pool_name="my_pool",
                                                                          pool_size=32,
                                                                          pool_reset_session=True,
                                                                          host=self.DB_HOST, port=self.DB_PORT,
                                                                          database=self.DB_DATABASE,
                                                                          user=self.DB_USER,
                                                                          password=self.DB_PASSWORD)

            connection_object = self.connection_pool.get_connection()

            if connection_object.is_connected():
                db_Info = connection_object.get_server_info()
                cursor = connection_object.cursor()
                cursor.execute("select database();")
                record = cursor.fetchone()

        except Error as e:
            self.logger.critical("Error while connecting to MySQL using Connection pool ", e)
        finally:
            # closing database connection.
            if (connection_object.is_connected()):
                cursor.close()
                connection_object.close()

    def process_updates(self):
        self.bot.process_new_updates([telebot.types.Update.de_json(request.stream.read().decode("utf-8"))])
        return "!", 200

    def webhook(self):
        self.bot.remove_webhook()
        self.bot.set_webhook(url=self.BASE_URL + self.TELEBOT_URL + self.TG_BOT_TOKEN)
        return "!", 200

    # method for inserts|updates|deletes
    def db_execute(self, query, params, comment=""):
        error_code = 1
        try:
            self.logger.debug("db_execute() " + comment)
            connection_local = self.connection_pool.get_connection()
            if connection_local.is_connected():
                cursor_local = connection_local.cursor()
                result = cursor_local.execute(query, params)
                connection_local.commit()
                error_code = 0
        except mysql.connector.Error as error:
            connection_local.rollback()  # rollback if any exception occured
            self.logger.warning("Failed {}".format(error))
        finally:
            # closing database connection.
            if (connection_local.is_connected()):
                cursor_local.close()
                connection_local.close()
        if error_code == 0:
            return True
        else:
            return False

    # method for selects
    def db_query(self, query, params, comment=""):
        try:
            self.logger.debug("db_query() " + comment)
            connection_local = self.connection_pool.get_connection()
            if connection_local.is_connected():
                cursor_local = connection_local.cursor()
                cursor_local.execute(query, params)
                result_set = cursor_local.fetchall()

                self.logger.debug("db_query().result_set:" + str(result_set))
                if result_set is None or len(result_set) <= 0:
                    result_set = []
                cursor_local.close()
        except mysql.connector.Error as error:
            self.logger.warning("Failed {}".format(error))
            result_set = []
        finally:
            # closing database connection.
            if (connection_local.is_connected()):
                connection_local.close()
        return result_set

    def run_bot(self):
        if self.env == 'heroku':
            while True:
                try:
                    self.logger.info("Server run. Version: " + VERSION)
                    self.webhook()
                    self.duplicate_controll.run()
                    self.server.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
                except Exception as e:
                    self.logger.critical("Cant start OCRhBot. RECONNECT" + str(e))
                    time.sleep(2)
        if self.env == 'local':
            while True:
                try:
                    self.bot.remove_webhook()
                    self.duplicate_controll.run()
                    self.logger.info("Server run. Version: " + VERSION)
                    self.bot.polling()
                except Exception as e:
                    self.logger.critical("Cant start OCRBot. RECONNECT " + str(e))
                    time.sleep(5)

    def run(self):

        self.t_processing = threading.Thread(target=self.scheduled_processing, name="Processingll")
        self.t_processing.start()
        self.t_bot = threading.Thread(target=self.run_bot(), name="Bot")
        self.t_bot.start()

        while True:
            time.sleep(10)

    def command_start(self, message):
        self.logger.info("Receive Start command from chat ID:" + str(message.chat.id))
        if message.from_user.username is not None:
            user_name = message.from_user.username
        else:
            user_name = message.from_user.first_name

        if self.new_user(message.chat.id, user_name):
            welcome_text = self.db_query("select value from ocr_bot_properties where name = %s", ("welcome_text",),
                                       "Get welcome text")[0][0]
            self.bot.send_message(message.chat.id,  welcome_text,
                                  reply_markup=self.inline_keyboard(self.inline_command_understand),parse_mode='Markdown',disable_web_page_preview=True)
            self.bot.send_message(self.ADMIN_ID, "New user: " + str(user_name))
        else:
            come_back_text = self.db_query("select value from ocr_bot_properties where name = %s", ("come_back_text",),"Get come back text")[0][0]
            dynamic_markup_commands = self.get_markup_commands(message.chat.id)
            self.bot.send_message(message.chat.id, come_back_text, reply_markup=self.markup_keyboard(dynamic_markup_commands),parse_mode='Markdown',disable_web_page_preview=True)
            self.db_execute("update ocr_bot_users set blocked = 0 where user_id = %s", (message.chat.id,), "User unblock")

    def new_user(self, user_id, user_name):
        if len(self.db_query("select user_id from ocr_bot_users where user_id=%s", (user_id,),
                             "Check User exist")) > 0:
            return False
        # add user:
        elif self.db_execute("insert into ocr_bot_users (name,user_id) values (%s,%s)", (user_name, user_id),
                             "Add new User"):
            return True
        else:
            return False

    def command_help(self, message):
        try:
            self.logger.info("Receive Help command from chat ID:" + str(message.chat.id))
            help_text = self.db_query("select value from ocr_bot_properties where name = %s", ("help_text",),"Get help text")[0][0]
            dynamic_markup_commands = self.get_markup_commands(message.chat.id)
            self.bot.send_message(message.chat.id, help_text + "\n\nVersion: " + VERSION,disable_web_page_preview=True, reply_markup=self.markup_keyboard(dynamic_markup_commands))
        except Exception as e:
            self.logger.critical("Cant execute Help command. " + str(e))
        return

    def command_stop(self, message):
        try:
            self.logger.info("Receive Stop command from chat ID:" + str(message.chat.id))
            self.bot.send_message(self.ADMIN_ID, "Stop user: " + str(message.chat.id))
            self.db_execute("delete from ocr_bot_users where user_id = %s", (message.chat.id,),"Delete User")
            self.bot.send_message(message.chat.id, "I forgot you. You can /start again", disable_web_page_preview=True, reply_markup=self.markup_keyboard([],remove=True))
        except Exception as e:
            self.logger.critical("Cant execute Stop command. " + str(e))
        return

    def broadcast(self, message):
        for item in self.db_query("select user_id from ocr_bot_users where blocked = '0'", (), "Get all Users"):
            try:
                dynamic_markup_commands = self.get_markup_commands(item[0])
                self.bot.send_message(item[0], message, reply_markup=self.markup_keyboard(dynamic_markup_commands))
                self.logger.info("Successfully sent broadcast for user:" + str(item[0]))
            except Exception as e:
                self.logger.warning("Cant send broadcast message for user:" + str(item[0])+ "; " + str(e))
                self.db_execute("update ocr_bot_users set blocked = '1', reason = %s where user_id = %s", (str(e)[0:299],item[0]),"Broadcast() Set user Blocked")

    def markup_keyboard(self, list_, remove=False, row_width=2):
        if not remove:
            markupkeyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=row_width)
            markupkeyboard.add(*[telebot.types.KeyboardButton(name) for name in list_])
        else:
            markupkeyboard = telebot.types.ReplyKeyboardRemove(selective=False)
        return markupkeyboard

    def inline_keyboard(self, list_, row_width=2):
        inlinekeyboard = telebot.types.InlineKeyboardMarkup(row_width=row_width)
        inlinekeyboard.add(*[telebot.types.InlineKeyboardButton(text=name, callback_data=name) for name in list_])
        return inlinekeyboard
    #
    # business logic part
    #
    def search_fns(self, query):
        self.logger.info("Search FNS: " + query)
        try:
            r = requests.get(
                'https://api-fns.ru/api/search',
                params={'q': query, 'key' : ''})
            json_data = json.loads(r.text)
            data = self.process_fns_data(json_data)
            return data
        except Exception as e:
            data = {}
            data["result"] = False
            self.logger.error("search fns:" + str(e))
            return data

    def process_fns_data(self, result_json):
        data = {}
        data["result"] = False
        
        return data

    def searchcompanies_in_db(self, text_):
        query = "select name, address, orgn from ocr_bot_companies where (fullname like \'%" + text_ + "%\' or fullname_t like \'%" + text_ + "%\' or name like \'%" + text_ + "%\' or name_t like \'%" + text_ + "%\') and status like '%Действующее%'"
        results = self.db_query(query,(),"Search company")
        data = []
        for result in results:
            data.append({'name':result[0], 'address':result[1], 'ogrn':result[2]})
        return data

    def file_to_text(self, image_filename):
        # загрузка изображения в виде объекта image Pillow, применение OCR
        text = pytesseract.image_to_string(Image.open(image_filename), lang='rus')
        return text

    def parse_text(self, text_):
        result = {}
        print("TXT", text_)
        wrap_text = text_.replace("\n"," ").replace("  "," ")
        wrap_text = wrap_text.replace(" 000 "," ООО ").replace(" О00 "," ООО ").replace(" 0О0 "," ООО ").replace(" 00О "," ООО ").replace(" ОО0 "," ООО ").replace(" 0ОО "," ООО ")
        wrap_text = wrap_text.replace("«","\"").replace("»","\"").replace("\'","\"").replace("”","\"")
        print("WRAP", wrap_text)
        legal_enties = self.find_legal_enties(wrap_text)
        ads_facts = self.find_ads_factors(wrap_text)
        result["legal_enties"] = legal_enties
        result["ads_facts"] = ads_facts
        if ads_facts["thrdparty"]:
            result["risk_name"] = 'high'
            result["risk_icon"] = self.risk_high_icon
        elif ads_facts["ads"]:
            result["risk_name"] = 'medium'
            result["risk_icon"] = self.risk_medium_icon
        else:
            result["risk_name"] = 'low'
            result["risk_icon"] = self.risk_low_icon
        return result

    # проверяет нахождение любого элемента листа в строке
    def check_in_list(self, list_, str_):
        for item in list_:
            if str_.lower().find(item) != -1:
                return True
        return False

    def find_legal_enties(self, text_):
        items = []
        return items

    def get_legal_enties(self, text):
        result = []
        return result

    def find_ads_factors(self, text_):
        text_ = text_.lower()
        data = {}
        data["ads"] = False
        data["thrdparty"] = False
        return data

    def process_text(self, user_id, text_):
        try:
            result = self.parse_text(text_)
            legal_entities = result["legal_enties"]
            if result["risk_name"] == 'high':
                # высокий риск
                if len(legal_entities) > 0:
                    # подсвечиваем компании иконкой
                    marked_legal_entities = []
                    for legal_entitie in legal_entities:
                        marked_legal_ent = self.risk_high_icon + " " + legal_entitie["name"]# + "(ОГРН: " + legal_entitie["ogrn"] + ")"
                        marked_legal_ent = marked_legal_ent[:30]
                        marked_legal_entities.append(marked_legal_ent)
                    marked_legal_entities = list(set(marked_legal_entities))
                    found_legal_entites_high_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_legal_entites_high_text",), "Get come back text")[0][0]
                    self.bot.send_message(user_id, found_legal_entites_high_text, reply_markup=self.inline_keyboard(marked_legal_entities))
                else:
                    found_no_legal_entites_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_no_legal_entites_text",), "Get come back text")[0][0]
                    self.bot.send_message(user_id, found_no_legal_entites_text)
                return
            if result["risk_name"] == 'medium':
                # средний риск
                if len(legal_entities) > 0:
                    # подсвечиваем компании иконкой
                    marked_legal_entities = []
                    for legal_entitie in legal_entities:
                        marked_legal_ent = self.risk_medium_icon + " " + legal_entitie["name"]# + "(ОГРН: " + legal_entitie["ogrn"] + ")"
                        marked_legal_ent = marked_legal_ent[:30]
                        marked_legal_entities.append(marked_legal_ent)
                    marked_legal_entities = list(set(marked_legal_entities))
                    found_legal_entites_medium_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_legal_entites_medium_text",), "Get come back text")[0][0]
                    self.bot.send_message(user_id, found_legal_entites_medium_text, reply_markup=self.inline_keyboard(marked_legal_entities))
                else:
                    found_no_legal_entites_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_no_legal_entites_text",), "Get come back text")[0][0]
                    self.bot.send_message(user_id, found_no_legal_entites_text)
                return
            if result["risk_name"] == 'low':
                # низкий риск
                if len(legal_entities) > 0:
                    # подсвечиваем компании иконкой
                    marked_legal_entities = []
                    for legal_entitie in legal_entities:
                        marked_legal_ent = self.risk_low_icon + " " + legal_entitie["name"]# + "(ОГРН: " + legal_entitie["ogrn"] + ")"
                        marked_legal_ent = marked_legal_ent[:30]
                        marked_legal_entities.append(marked_legal_ent)
                    marked_legal_entities = list(set(marked_legal_entities))
                    found_legal_entites_low_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_legal_entites_low_text",), "Get come back text")[0][0]
                    self.bot.send_message(user_id, found_legal_entites_low_text, reply_markup=self.inline_keyboard(marked_legal_entities))
                else:
                    found_no_legal_entites_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_no_legal_entites_text",), "Get come back text")[0][0]
                    self.bot.send_message(user_id, found_no_legal_entites_text)
                return
        except Exception as e:
            self.logger.error("Process text: " + str(e))
            self.bot.send_message(user_id, "Что-то не получилось, мы уже разбираемся")
            return

    def create_personal_data_recall_doc(self, company_short_name, company_full_name, company_address, fio_short, fio_full, address, passport, passport_issued):
        data = {}
        data["isvalid"] = False

        # company_name = company_name.replace("= ","")

        # строим файл с отчетом
        try:
            doc = DocxTemplate(config.PD_RECALL_TEMPLATE_FILE)

            context = {'company_name': company_full_name,
                       'fio_short': fio_short,
                       'fio_full': fio_full,
                       'passport': passport,
                       'passport_issued': passport_issued,
                       'address': address,
                       'company_address': company_address,
                       'date': str((datetime.datetime.now()).strftime("%d.%m.%Y"))}

            doc.render(context)
            # генерируем имя файла
            report_filename = company_short_name.lower().replace("\"","") + "_pd_recall_" + str((datetime.datetime.now()).strftime("%Y-%m-%d"))
            # оно должно быть уникальным, поэтому првоеряем на наличие пока не найдем свободное
            i = 0
            while True:
                if os.path.exists(config.TMP_PATH + report_filename + ".docx"):
                    i = i + 1
                    if i == 1:
                        report_filename = report_filename + "_" + str(i)
                    else:
                        report_filename = report_filename[:-1] + str(i)
                else:
                    break
            doc.save(config.TMP_PATH + report_filename + ".docx")
            # передаем ссылку на файл обратно для отправки
            data["report_filepath"] = config.TMP_PATH + report_filename + ".docx"
        except Exception as e:
            self.logger.warning("Problem with save docs:" + str(e))
            return data

        # если дошли сюда - значит все хорошо и можно передавать ОК
        data["isvalid"] = True
        return data

    def get_markup_commands(self, user_id):
        try:
            state = self.db_query("select state from ocr_bot_users where user_id=%s", (user_id,), "Get State")[0][0]
        except Exception as e:
            self.logger.error("Empty state on user: " + str(user_id))
            state = ''

        markup_commands = [self.markup_command_legal_list,'Настройки','Помощь','Приватность']

        return markup_commands

    def scheduled_processing(self):
        while True:
            time_now = datetime.datetime.now() - datetime.timedelta(seconds=self.processing_interval_sec)
            sessions = self.db_query('''select session_id, user_id, tmp_data from ocr_bot_user_data_tmp where state = '' and create_time <= %s''', (str(time_now),), "Get all sessions")
            for session in sessions:
                # процессим сообщения
                self.process_text(session[1], session[2])
                # закрываем сессии
                self.db_execute("update ocr_bot_user_data_tmp set state = 'closed' where session_id = %s", (session[0],), "Close session")
                self.logger.info("session closed:" + str(session[0]))
            time.sleep(2)

    def command_view_legal_entites_list(self, message):
        legal_entites_list = []
        legal_entites_list_tmp = self.db_query("select legal_entities from ocr_bot_users where user_id=%s", (message.chat.id,), "Get State")[0][0]
        if len(legal_entites_list_tmp) > 2:
            legal_entites_list = legal_entites_list_tmp.split("|")
            marked_legal_entites = []
            for legal_entite in legal_entites_list:
                marked_legal_ent = legal_entite
                if legal_entite.startswith("$3="): marked_legal_ent = " " + self.risk_high_icon + legal_entite[3:]
                if legal_entite.startswith("$2="): marked_legal_ent = " " + self.risk_medium_icon + legal_entite[3:]
                if legal_entite.startswith("$1="): marked_legal_ent = " " + self.risk_low_icon + legal_entite[3:]
                marked_legal_ent = marked_legal_ent[:30]
                marked_legal_entites.append(marked_legal_ent)
            show_legal_entites_text = self.db_query("select value from ocr_bot_properties where name = %s", ("show_legal_entites_text",), "Get come back text")[0][0]
            self.bot.send_message(message.chat.id, show_legal_entites_text, reply_markup=self.inline_keyboard(marked_legal_entites))
        else:
            show_nolegal_entites_text = self.db_query("select value from ocr_bot_properties where name = %s", ("show_nolegal_entites_text",), "Get come back text")[0][0]
            dynamic_markup_commands = self.get_markup_commands(message.chat.id)
            self.bot.send_message(message.chat.id, show_nolegal_entites_text, reply_markup=self.markup_keyboard(dynamic_markup_commands),parse_mode='Markdown')
        return

    def command_show_settings(self, message):
        try:
            self.logger.info("Receive Settings command from chat ID:" + str(message.chat.id))
            settings_text = self.db_query("select value from ocr_bot_properties where name = %s", ("settings_text",),"Get settings_text")[0][0]
            dynamic_markup_commands = self.get_markup_commands(message.chat.id)
            self.bot.send_message(message.chat.id, settings_text,disable_web_page_preview=True, reply_markup=self.markup_keyboard(dynamic_markup_commands))
        except Exception as e:
            self.logger.critical("Cant execute Settings command. " + str(e))
        return

    def command_show_legal(self, message):
        try:
            self.logger.info("Receive Legal command from chat ID:" + str(message.chat.id))
            legal_text = self.db_query("select value from ocr_bot_properties where name = %s", ("legal_text",),"Get settings_text")[0][0]
            dynamic_markup_commands = self.get_markup_commands(message.chat.id)
            self.bot.send_message(message.chat.id, legal_text,disable_web_page_preview=True, reply_markup=self.markup_keyboard(dynamic_markup_commands),parse_mode='Markdown')
        except Exception as e:
            self.logger.critical("Cant execute Legal command. " + str(e))
        return

    def personal_data_validation(self, text_):
        data = {}
        data["isvalid"] = False
        # разбиваем текст на словарь
        try:
            # разбиваем текст на словарь
            for line in text_.split("\n"):
                key = line[0:line.find(":")].strip()
                value = line[line.find(":") + 1:].strip()
                if len(key) > 0: data[key] = value
        except Exception as e:
            self.logger.warning("Receive message:" + str(e))
            return data
        mandatory_fields_list = ['ShortName', 'FullName', 'Address' , 'Passport', 'IssuedBy']
        for field in mandatory_fields_list:
            if data.get(field) is None:
                return data
        data["isvalid"] = True
        return data

    def handle_messages(self, messages):
        for message in messages:
            try:
                if message.content_type in ['document']:
                    
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    file_name = message.document.file_name
                    file_id_info = self.bot.get_file(message.document.file_id)
                    downloaded_file = self.bot.download_file(file_id_info.file_path)
                    src = config.TMP_PATH + file_name
                    with open(src, 'wb') as new_file:
                        new_file.write(downloaded_file)
                    if src.lower().endswith("png") or src.lower().endswith("jpg") or src.lower().endswith("jpeg"):
                        if os.path.getsize(src)/1024/1024 > 1:
                            self.bot.send_message(message.chat.id, "Файл слишком большой, приложите файл менее 1 Мб")
                            return
                        text_ = self.file_to_text(src)
                        self.process_text(message.chat.id, text_)
                    else:
                        self.bot.send_message(message.chat.id, "Я умею разбирать только картинки форматов PNG и JPG. " + str(file_name))
                    return
                if message.content_type in ['photo']:
                    
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    file_id_info = self.bot.get_file(message.photo[-1].file_id)
                    downloaded_file = self.bot.download_file(file_id_info.file_path)
                    src = config.TMP_PATH + str(message.chat.id) + "_" + str(datetime.datetime.now().timestamp()) + ".png"
                    with open(src, 'wb') as new_file:
                        new_file.write(downloaded_file)
                    if os.path.getsize(src) / 1024 / 1024 > 1:
                        self.bot.send_message(message.chat.id, "Фото слишком большое, приложите файл менее 1 Мб или попробуйте отправить в формате \"как фото\"")
                        return
                    text_ = self.file_to_text(src)
                    self.process_text(message.chat.id, text_)
                    return
                if message.content_type != 'text':
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    dynamic_markup_commands = self.get_markup_commands(message.chat.id)
                    self.bot.reply_to(message, text="Круто! Но бот пока поддерживает только текстовые сообщения и картинки с текстом", reply_markup=self.markup_keyboard(dynamic_markup_commands),parse_mode='Markdown')
                    self.bot.send_message(self.ADMIN_ID, "New NONTXT message from " + str(message.chat.id))
                    print(message)
                    return
                if self.duplicate_controll.in_cache(message.chat.id, message.text):
                    self.logger.warning("Diplicate message from user" + str(message.chat.id) + "\n" + message.text)
                    continue

                # processing
                if message.text.startswith("/start"):
                    self.command_start(message)
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id),"Reset State")
                    return
                if message.text.startswith("/help") or message.text.startswith("Помощь"):
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    self.command_help(message)
                    return
                if message.text.startswith("/stop"):
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    self.command_stop(message)
                    return
                if message.text.startswith("Настройки"):
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    user_data = self.db_query("select fio_short, fio_full, address, passport, passport_issued from ocr_bot_users where user_id=%s", (message.chat.id,), "Get User Data")
                    if len(user_data) > 0:
                        user_pd = "*ShortName:* " + user_data[0][0] + "\n" + \
                                  "*FullName:* " + user_data[0][1] + "\n" + \
                                  "*Address:* " + user_data[0][2] + "\n" + \
                                  "*Passport:* " + user_data[0][3] + "\n" + \
                                  "*IssuedBy:* " + user_data[0][4]
                    else:
                        user_pd = "*ShortName:* " + "\n" + \
                                  "*FullName:* "  + "\n" + \
                                  "*Address:* " + "\n" + \
                                  "*Passport:* "  + "\n" + \
                                  "*IssuedBy:* "
                    message_text = "*Данные для шаблона*:\n" + user_pd
                    self.bot.send_message(message.chat.id, message_text, reply_markup=self.inline_keyboard(self.settings_commands), parse_mode='Markdown')
                    return
                if message.text.startswith("Приватность") or message.text.startswith("/privacy"):
                    self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    self.command_show_legal(message)
                    return
                if message.text.startswith("/broadcast"):
                    if int(message.chat.id) == int(self.ADMIN_ID):
                        self.broadcast(message.text.replace("/broadcast ", ""))
                    else:
                        self.bot.reply_to(message, "You are not admin")
                    return

                if message.text.startswith(self.markup_command_legal_list):
                    self.command_view_legal_entites_list(message)
                    return

                # проверка на статусы:
                try:
                    state = self.db_query("select state from ocr_bot_users where user_id=%s", (message.chat.id,), "Get State")[0][0]
                except:
                    state = ''

                if state == "wait_pd":
                    data = self.personal_data_validation(message.text)
                    if data["isvalid"]:
                        # update user data
                        self.db_execute("update ocr_bot_users set fio_short = %s, fio_full = %s, address  = %s, passport  = %s, passport_issued  = %s where user_id = %s",
                                        (data['ShortName'], data['FullName'], data['Address'], data['Passport'], data['IssuedBy'], message.chat.id), "Update user data")
                        dynamic_markup_commands = self.get_markup_commands(message.chat.id)
                        self.bot.send_message(message.chat.id, "Данные обновлены", reply_markup=self.markup_keyboard(dynamic_markup_commands), parse_mode='Markdown')
                        self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("", message.chat.id), "Reset State")
                    else:
                        pd_template = "ShortName: Иванов И.И." + "\n" + \
                                      "FullName: Иванов Иван Иванович" + "\n" + \
                                      "Address: Москва, Красная пл., 1" + "\n" + \
                                      "Passport: 4100 800900" + "\n" + \
                                      "IssuedBy: 31.12.2000г. Отделением УМВД РФ по г. Москве"
                        dynamic_markup_commands = self.get_markup_commands(message.chat.id)
                        self.bot.send_message(message.chat.id, "Неправильный формат. Напишите данные в таком формате:\n" + pd_template, reply_markup=self.markup_keyboard(dynamic_markup_commands))
                    return

                # Если ничего не сработало, проверим длину сообщения c учетом дребезга
                if len(message.text) > 5 and len(message.text) < 50:
                    final_items = []
                    item = message.text.replace("«","\"").replace("»","\"").replace("\'","\"").replace("”","\"")
                    # проверяем данные в нашей БД
                    search_results = self.searchcompanies_in_db(item)
                    if len(search_results) > 0:
                        # нашли в нашей БД, добавляем все найденное в список
                        final_items.extend(search_results)
                    else:
                        # не нашли в нащей БД, идем в ФНС
                        search_fns_result = self.search_fns(item)
                        if search_fns_result['result']:
                            # что-то добавили из ФНС, повторяем поиск
                            search_results = self.searchcompanies_in_db(item)
                            if len(search_results) > 0:
                                # нашли в нашей БД, добавляем все найденное в список
                                final_items.extend(search_results)
                            else:
                                # не нашли в нашей БД после срабатывания, надо разбираться
                                self.logger.warning("Company not found after FNS update:" + item)
                        else:
                            # в ФНС ничего не смогли найти, скорре всего ошибка распознавания при парсинге
                            self.logger.warning("Company not found in FNS:" + item)

                    if len(final_items) > 0:
                        # подсвечиваем компании иконкой
                        marked_legal_entities = []
                        for legal_entitie in final_items:
                            marked_legal_ent = self.risk_low_icon + " " + legal_entitie["name"]# + " (ОГРН: " + legal_entitie["ogrn"] + ")"
                            marked_legal_ent = marked_legal_ent[:30]
                            marked_legal_entities.append(marked_legal_ent)
                        marked_legal_entities = list(set(marked_legal_entities))
                        found_legal_entites_custom_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_legal_entites_custom_text",), "Get come back text")[0][0]
                        self.bot.send_message(message.chat.id, found_legal_entites_custom_text, reply_markup=self.inline_keyboard(marked_legal_entities, row_width=1))
                    else:
                        # ничего не нашли
                        found_no_legal_entites_text = self.db_query("select value from ocr_bot_properties where name = %s", ("found_no_legal_entites_text",), "Get come back text")[0][0]
                        dynamic_markup_commands = self.get_markup_commands(message.chat.id)
                        self.bot.send_message(message.chat.id, found_no_legal_entites_text, reply_markup=self.markup_keyboard(dynamic_markup_commands), parse_mode='Markdown')
                    return
                else:
                # считаем, то юзер вставил текст для анализа :
                    # проверяем есть ли открытая сессия у юзера:
                    session_id_result = self.db_query('''select session_id,tmp_data from ocr_bot_user_data_tmp where user_id = %s and state = '' ''', (message.chat.id,), "Get session ID")
                    print(len(message.text))
                    if len(session_id_result) > 0:
                        # session exist
                        session_id = session_id_result[0][0]
                        # дописываем текст в сессию
                        tmp_data = session_id_result[0][1] + " " + message.text
                        # обновляем обратно
                        self.db_execute("update ocr_bot_user_data_tmp set tmp_data = %s, create_time=CURRENT_TIMESTAMP where session_id = %s",(tmp_data, int(session_id)), "Update user data")
                    elif len(message.text) > 3500:
                        # юзер кинул длинное сообщение, нужно создать сессию
                        # создаем сессию
                        self.db_execute("insert into ocr_bot_user_data_tmp (user_id, tmp_data) value (%s,%s)",(message.chat.id, message.text), "Create session")
                    else:
                        # сообщение короткое, отвечаем сразу
                        self.process_text(message.chat.id, message.text)

            except Exception as e:
                self.logger.warning("Cant process message:" + str(message) + str(e))
                dynamic_markup_commands = self.get_markup_commands(message.chat.id)
                self.bot.reply_to(message, text="Ой, это непонятная ветка, напиште плиз @MichaelPavlov\n" + str(e), reply_markup=self.markup_keyboard(dynamic_markup_commands),
                                  parse_mode='Markdown')

    def handle_callback_messages(self, callback_message):
        # обязательный ответ в API
        self.bot.answer_callback_query(callback_message.id)

        self.bot.send_message(self.ADMIN_ID, "New inline message from " + str(callback_message.message.chat.id) + "\n" + callback_message.data)

        # сначала смотрим статус юзера, от этого зависит как интерпретировать команду
        try:
            state = self.db_query("select state from ocr_bot_users where user_id=%s", (callback_message.message.chat.id,), "Get State")[0][0]
        except Exception as e:
            self.logger.error("Empty state on message" + str(callback_message.data) + "; " + str(callback_message.message.chat.id))
            state = ''

        # команда ввода новых данных
        if callback_message.data == self.settings_edit_pd:
            # запоминаем и ставим статус с ожиданием
            self.db_execute("update ocr_bot_users set state = %s where user_id = %s", ("wait_pd", callback_message.message.chat.id), "Update State")
            pd_template = "ShortName: Иванов И.И." + "\n" + \
                          "FullName: Иванов Иван Иванович"  + "\n" + \
                          "Address: Москва, Красная пл., 1" + "\n" + \
                          "Passport: 4100 800900"  + "\n" + \
                          "IssuedBy: 31.12.2000г. Отделением УМВД РФ по г. Москве"
            self.bot.send_message(callback_message.message.chat.id, "Напишите данные в таком формате:\n" + pd_template, reply_markup=self.markup_keyboard([],remove=True))
            return

        # команда удаления данных
        if callback_message.data == self.settings_delete_pd:
            # update user data
            default_pd = '______________'
            self.db_execute("update ocr_bot_users set fio_short = %s, fio_full = %s, address  = %s, passport  = %s, passport_issued  = %s where user_id = %s",
                            (default_pd, default_pd, default_pd, default_pd, default_pd, callback_message.message.chat.id), "Update user data")
            dynamic_markup_commands = self.get_markup_commands(callback_message.message.chat.id)
            self.bot.send_message(callback_message.message.chat.id, "Данные удалены", reply_markup=self.markup_keyboard(dynamic_markup_commands))
            return

        if callback_message.data.startswith("Понятно"):
            dynamic_markup_commands = self.get_markup_commands(callback_message.message.chat.id)
            self.bot.send_message(callback_message.message.chat.id, "Жду текст или фотку", reply_markup=self.markup_keyboard(dynamic_markup_commands), parse_mode='Markdown')
            return

        # значит юзер нажал на компанию и хочет сформировать документ
        if callback_message.data.startswith(" "):
            # восстанавливаем имя компании без обрезки
            query = "select name, address, orgn, fullname from ocr_bot_companies where (name like \'" + callback_message.data[2:] + "%\') and status like '%Действующее%'"
            results = self.db_query(query, (), "Search company")
            if len(results) == 0:
                self.bot.send_message(callback_message.message.chat.id, "Что-то пошло не так")
                self.logger.error("No company for: " + callback_message.data)
                return
            # берем первую
            company_short_name = results[0][0]
            company_full_name = results[0][3]
            company_address = results[0][1]
            # TODO спрашиваем паспортные данные
            # проверяем на нужный формат
            user_data = self.db_query("select fio_short, fio_full, address, passport, passport_issued from ocr_bot_users where user_id=%s", (callback_message.message.chat.id,), "Get username")
            data = self.create_personal_data_recall_doc(company_short_name, company_full_name, company_address, user_data[0][0], user_data[0][1], user_data[0][2], user_data[0][3], user_data[0][4])
            if data["isvalid"]:
                print("good, path is:", data["report_filepath"])
                doc = open(data["report_filepath"], 'rb')
                self.bot.send_document(callback_message.message.chat.id, doc)
                self.logger.info("Create new doc from user:" + str(callback_message.message.chat.id) + "; file:" + data["report_filepath"])
            else:
                self.bot.send_message(callback_message.message.chat.id, "Unknown error. =(")
                self.logger.info("Unknown format from user:" + str(callback_message.message.chat.id) + "; text:" + callback_message.data)
            return

        # значит юзер нажал на компанию и хочет добавить ее в список
        if callback_message.data.startswith(self.risk_high_icon) or callback_message.data.startswith(self.risk_medium_icon) or callback_message.data.startswith(self.risk_low_icon):
            # Компания уже есть в БД, просто добавляем ее в профиль юзера
            # дернем сразу команды
            dynamic_markup_commands = self.get_markup_commands(callback_message.message.chat.id)
            # достаем список:
            legal_entites_list = []
            legal_entites_list_tmp = self.db_query("select legal_entities from ocr_bot_users where user_id=%s", (callback_message.message.chat.id,), "Get State")[0][0]
            if len(legal_entites_list_tmp) > 2:
                legal_entites_list = legal_entites_list_tmp.split("|")
            # восстанавливаем имя компании без обрезки
            query = "select name, address, orgn from ocr_bot_companies where (name like \'" + callback_message.data[2:] + "%\') and status like '%Действующее%'"
            results = self.db_query(query, (), "Search company")
            if len(results) == 0:
                self.bot.send_message(callback_message.message.chat.id, "Что-то пошло не так")
                self.logger.error("No company for: " + callback_message.data[2:])
                return
            # берем первую
            company_name = results[0][0]
            # добавляем компанию, добавляем пробел для различимости кейсов
            if callback_message.data.startswith(self.risk_high_icon): company_name = "$3=" + company_name
            if callback_message.data.startswith(self.risk_medium_icon): company_name = "$2=" + company_name
            if callback_message.data.startswith(self.risk_low_icon): company_name = "$1=" + company_name
            legal_entites_list.append(company_name)
            # TODO могут существовать две одинаковые компании с разным риском
            legal_entites_list = list(set(legal_entites_list))
            # записываем данные
            self.db_execute("update ocr_bot_users set legal_entities=%s where user_id=%s", ('|'.join(legal_entites_list), callback_message.message.chat.id), "Get State")
            # Отправялем подтверждение
            message_text = "Добавил компанию " + company_name[3:] + " в ваш список"
            self.bot.send_message(callback_message.message.chat.id, message_text, reply_markup=self.markup_keyboard(dynamic_markup_commands), parse_mode='Markdown')
            return

        self.logger.warning("No handler for:" + callback_message.data + ";")

        return


if __name__ == '__main__':
    mnBot = OCRBot(env='local', mode='online', proxy=False)
    mnBot.run()
