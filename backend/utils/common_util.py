from sqlalchemy.engine.row import Row


class CamelCaseUtil:
    """
    下划线形式(snake_case)转小驼峰形式(camelCase)工具方法
    """

    @classmethod
    def snake_to_camel(cls, snake_str):
        """
        下划线形式字符串(snake_case)转换为小驼峰形式字符串(camelCase)

        :param snake_str: 下划线形式字符串
        :return: 小驼峰形式字符串
        """
        # 分割字符串
        # words = snake_str.split('_')
        # # 小驼峰命名，第一个词首字母小写，其余词首字母大写
        # return words[0] + ''.join(word.capitalize() for word in words[1:])
        return snake_str

    @classmethod
    def transform_result(cls, result):
        """
        针对不同类型将下划线形式(snake_case)批量转换为小驼峰形式(camelCase)方法

        :param result: 输入数据
        :return: 小驼峰形式结果
        """
        if result is None:
            return result
        # 如果是字典，直接转换键
        elif isinstance(result, dict):
            return {cls.snake_to_camel(k): v for k, v in result.items()}
        # 如果是一组字典或其他类型的列表，遍历列表进行转换
        elif isinstance(result, list):
            return [
                cls.transform_result(row)
                if isinstance(row, (dict, Row))
                else (
                    cls.transform_result({c.name: getattr(row, c.name) for c in row.__table__.columns}) if row else row
                )
                for row in result
            ]
        # 如果是sqlalchemy的Row实例，遍历Row进行转换
        elif isinstance(result, Row):
            return [
                cls.transform_result(row)
                if isinstance(row, dict)
                else (
                    cls.transform_result({c.name: getattr(row, c.name) for c in row.__table__.columns}) if row else row
                )
                for row in result
            ]
        # 如果是其他类型，如模型实例，先转换为字典
        else:
            return cls.transform_result({c.name: getattr(result, c.name) for c in result.__table__.columns})