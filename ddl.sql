create table ocr_bot_companies
(
	inn varchar(20) null,
	orgn varchar(20) not null,
	name varchar(300) null,
	name_t varchar(300) null,
	fullname varchar(300) null,
	fullname_t varchar(300) null,
	idx varchar(20) null,
	address varchar(300) null,
	status varchar(200) null,
	create_time timestamp default CURRENT_TIMESTAMP not null,
	constraint ocr_bot_companies_orgn_uindex
		unique (orgn)
);

alter table ocr_bot_companies
	add primary key (orgn);

create table ocr_bot_properties
(
	name varchar(60) not null
		primary key,
	value varchar(2000) not null,
	description varchar(100) null
);

create table ocr_bot_user_data_tmp
(
	session_id int auto_increment,
	user_id varchar(30) not null,
	tmp_data text null,
	create_time timestamp default CURRENT_TIMESTAMP not null,
	state varchar(20) default '' null,
	constraint ocr_bot_user_data_tmp_session_id_uindex
		unique (session_id)
);

alter table ocr_bot_user_data_tmp
	add primary key (session_id);

create table ocr_bot_users
(
	name varchar(30) null,
	user_id varchar(30) not null
		primary key,
	state varchar(100) default '' null,
	tags varchar(2000) default '' null,
	blocked tinyint(1) default 0 not null,
	reason varchar(300) null,
	create_time timestamp default CURRENT_TIMESTAMP not null,
	allow_anonymous_chats tinyint(1) default 1 null,
	show_anonymous_chat_button tinyint(1) default 1 null,
	legal_entities varchar(1000) default '' null,
	fio_full varchar(200) default '______________' null,
	fio_short varchar(200) default '______________' null,
	address varchar(200) default '______________' null,
	passport varchar(20) default '______________' null,
	passport_issued varchar(200) default '______________' null
);

