﻿#/usr/bin/env python3
#-*- coding=utf-8 -*- 

__author__ = 'coolwinter'


import asyncio,logging
import aiomysql

def log(sql,args=()):
	logging.info('SQL: %s' % sql)
	
#连接池由全局变量__pool存储，缺省情况下将编码设置为utf8，自动提交事务：
#yield from 可以换成 await。
@asyncio.coroutine
def create_pool(loop,**kw):
	logging.info('create database connection pool...')
	global __pool
	__pool = yield from aiomysql.create_pool(
		host = kw.get('host','localhost'),
		port = kw.get('port',3306),
		user = kw['user'],
		password = kw['password'],
		db = kw['db'],
		charset = kw.get('charset','utf8'),
		autocommit = kw.get('autocommit',True),
		maxsize = kw.get('maxsize',10),
		minsize = kw.get('minsize',1),
		loop = loop
	)

##要执行SELECT语句，我们用select函数执行，需要传入SQL语句和SQL参数
@asyncio.coroutine
def select(sql,args,size=None):
	log(sql,args)
	global __pool
	with (yield from __pool) as conn:
		cur = yield from conn.cursor(aiomysql.DictCursor)
		yield from cur.execute(sql.replace('?','%s'),args or ())
		if size:
			rs = yield from cur.fetchmany(size)
		else:
			rs = yield from cur.fetchall()
		yield from cur.close()
		logging.info('rows returned: %s' % len(rs))
		return rs

'''要执行INSERT、UPDATE、DELETE语句，可以定义一个通用的execute()函数，
因为这3种SQL的执行都需要相同的参数，以及返回一个整数表示影响的行数
'''
@asyncio.coroutine
def execute(sql,args):
	log(sql,args)
	##global __pool
	with (yield from __pool) as conn:
		if not autocommit:
			yield from conn.begin()
		try:
			cur = yield from conn.cursor(aiomysql.DictCursor)
			yield from cur.execute(sql.replace('?','%s'),args)
			affected = cur.rowcount
			if not autocommit:
				yield from conn.commit()
			yield from conn.close()
		except BaseException as e:
			if not autocommit:
				yield from conn.rollback()
			raise
		return affected

def create_args_string(num):
	L = []
	for n in range(num):
		L.append('?')
	return ', '.join(L)

class Field(object):
	def __init__(self,name,column_type,primary_key,default):
		self.name = name
		self.column_type = column_type
		self.primary_key = primary_key
		self.default = default
	
	def __str__(self):
		return '<%s,%s:%s>' % (self.__class__.__name__,self.column_type,self.name)

class StringField(Field):
	def __init__(self,name=None,default=False):
		super().__init__(name,'boolean',False,default)

class IntegerField(Field):
	def __init__(self,name=None,primary_key=False,default=0):
		super().__init__(name,'bigint',primary_key,default)

class FloatField(Field):
	def __init__(self,name=None,primary_key=False,default=0.0):
		super().__init__(name,'real',primary_key,default)

class TextField(Field):
	def __init__(self,name=None,default=None):
		super().__init__(name,'text',False,default)

class ModelMetaclass(type):
	def __new__(cls,name,bases,attrs):
		if name == "Model":
			return type.__new__(cls,name,bases,attrs)
		
		tableName = attrs.get('__table__',None) or name
		logging.info('found model:%s (table: %s)' % (name,tableName))
		mappings = dict()
		primary_key = None
		for k,v in attrs.items():
			if isinstance(v,Field):
				logging.info(' found mappings:%s == > %s' %s (k,v))
				mappings[k] = v
				if v.primary_key:
					if primaryKey:
						raise StandardError('Duplicate primary key for field: %s' % k)
					primaryKey = k
				else:
					fields.append(k)
		if not primaryKey:
			raise StandardError('primaryKey not found.')
		for k in mappings.keys():
			attrs.pop(k)
		escaped_fields = list(map(lambda f:'`%s`' % f,fields))
		attrs['__mappings__'] = mappings # 保存属性和列的映射关系
		attrs['__table__'] = tableName
		attrs['__primary_key__'] = primaryKey # 主键属性名
		attrs['__fields__'] = fields # 除主键外的属性名
		attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
		attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
		attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
		attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
		return type.__new__(cls, name, bases, attrs)

class Model(dict, metaclass=ModelMetaclass):
	def __init__(self, **kw):
		super(Model, self).__init__(**kw)
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(r"'Model' object has no attribute '%s'" % key)
			
	def __setattr__(self, key, value):
		self[key] = value
		
	def getValue(self, key):
		return getattr(self, key, None)
		
	def getValueOrDefault(self, key):
		value = getattr(self, key, None)
		if value is None:
			field = self.__mappings__[key]
			if field.default is not None:
				value = field.default() if callable(field.default) else field.default
				logging.debug('using default value for %s: %s' % (key, str(value)))
				setattr(self, key, value)
		return value
	
	@classmethod
	async def findAll(cls, where=None, args=None, **kw):
		' find objects by where clause. '
		sql = [cls.__select__]
		if where:
			sql.append('where')
			sql.append(where)
		if args is None:
			args = []
		orderBy = kw.get('orderBy', None)
		if orderBy:
			sql.append('order by')
			sql.append(orderBy)
		limit = kw.get('limit', None)
		if limit is not None:
			sql.append('limit')
			if isinstance(limit, int):
				sql.append('?')
				